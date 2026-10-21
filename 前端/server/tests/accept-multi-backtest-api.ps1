param(
    [string]$BaseUrl = "http://localhost:3000",
    [string[]]$Strategies = @("technical", "rf", "xgboost", "gru"),
    [int]$TimeoutMinutes = 30
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd("/")
$AllowedStrategies = @("technical", "rf", "xgboost", "gru")
foreach ($strategy in $Strategies) {
    if ($strategy -notin $AllowedStrategies) { throw "不支援的策略 ID：$strategy" }
}
if ($Strategies.Count -eq 0 -or ($Strategies | Select-Object -Unique).Count -ne $Strategies.Count) {
    throw "請提供至少一個不重複的策略 ID。"
}

$script:PassCount = 0
$script:FailCount = 0
$script:FatalError = $null
function Write-Check([string]$Name, [bool]$Passed, [string]$Detail = "") {
    if ($Passed) { $script:PassCount++; Write-Host "PASS $Name $Detail" -ForegroundColor Green }
    else { $script:FailCount++; Write-Host "FAIL $Name $Detail" -ForegroundColor Red }
}

function Invoke-JsonApi([string]$Method, [string]$Uri, $Body = $null, $Headers = $null) {
    $parameters = @{ Method = $Method; Uri = $Uri; UseBasicParsing = $true; ErrorAction = "Stop" }
    if ($null -ne $Headers) { $parameters.Headers = $Headers }
    if ($null -ne $Body) {
        $parameters.ContentType = "application/json; charset=utf-8"
        $parameters.Body = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }
    return Invoke-RestMethod @parameters
}

$token = $null
$password = $null
$passwordBstr = [IntPtr]::Zero
try {
    $unauthStatus = 0
    try {
        Invoke-JsonApi -Method "Post" -Uri "$BaseUrl/api/backtests/multi/run" -Body @{ stock_code = "2330"; strategies = @("technical") } | Out-Null
    } catch {
        if ($_.Exception.Response) { $unauthStatus = [int]$_.Exception.Response.StatusCode }
    }
    Write-Check "未登入請求回傳 HTTP 401" ($unauthStatus -eq 401) "HTTP $unauthStatus"

    $email = Read-Host "輸入既有帳號電子郵件"
    $securePassword = Read-Host "輸入密碼" -AsSecureString
    $passwordBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordBstr)
    try {
        $login = Invoke-JsonApi -Method "Post" -Uri "$BaseUrl/api/auth/login" -Body @{ email = $email; password = $password }
    } catch {
        Write-Check "既有帳號登入" $false "登入失敗；請確認 API 與帳號狀態，沒有輸出登入資料。"
        throw "登入未成功，已停止驗收。"
    }
    if ([string]::IsNullOrWhiteSpace([string]$login.token)) {
        Write-Check "登入回傳 JWT" $false "回應未包含 token 欄位。"
        throw "登入回應格式不符，已停止驗收。"
    }
    $token = [string]$login.token
    Write-Check "既有帳號登入並取得記憶體 Token" $true
    $headers = @{ Authorization = "Bearer $token" }

    $requestBody = @{ stock_code = "2330"; strategies = $Strategies }
    $job = Invoke-JsonApi -Method "Post" -Uri "$BaseUrl/api/backtests/multi/run" -Body $requestBody -Headers $headers
    Write-Check "回測工作已建立" ($job.status -in @("pending", "running")) "狀態 $($job.status)"
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $state = $null
    do {
        Start-Sleep -Seconds 3
        $state = Invoke-JsonApi -Method "Get" -Uri "$BaseUrl$($job.status_url)" -Headers $headers
        Write-Host "工作狀態：$($state.status)"
    } while ($state.status -in @("pending", "running") -and (Get-Date) -lt $deadline)

    Write-Check "回測工作完成" ($state.status -eq "completed") "狀態 $($state.status)"
    if ($state.status -ne "completed") {
        $safeMessage = if ($state.error) { "$($state.error.code): $($state.error.message)" } else { "等待逾時或工作未完成" }
        Write-Host $safeMessage -ForegroundColor Yellow
        throw "回測未完成，沒有讀取結果。"
    }

    $result = Invoke-JsonApi -Method "Get" -Uri "$BaseUrl$($job.result_url)" -Headers $headers
    Write-Check "回傳 2330 historical_development" ($result.stock_code -eq "2330" -and $result.data_role -eq "historical_development")
    Write-Check "回測期間為核准開發期間" ($result.backtest_period.start -eq "2024-07-01" -and $result.backtest_period.end -eq "2024-12-30")
    Write-Check "回傳策略符合本次選取" ((@($result.strategies | ForEach-Object { $_.strategy_id }) -join ",") -eq ($Strategies -join ","))
    Write-Check "自動附上 Buy & Hold" ($result.buy_and_hold.strategy_id -eq "buy_and_hold")
    Write-Check "未使用 Final OOS" ($result.audit.final_oos_used -eq $false)

    $allCurves = New-Object 'System.Collections.Generic.List[object]'
    foreach ($strategyResult in $result.strategies) { $allCurves.Add($strategyResult.equity_curve) }
    $allCurves.Add($result.buy_and_hold.equity_curve)
    $axisOkay = $true
    $referenceAxis = @($allCurves[0] | ForEach-Object { $_.timestamp })
    foreach ($curve in $allCurves) {
        $axis = @($curve | ForEach-Object { $_.timestamp })
        if ($axis.Count -ne $referenceAxis.Count -or (($axis -join "|") -ne ($referenceAxis -join "|"))) { $axisOkay = $false }
    }
    Write-Check "策略與 Buy & Hold 共用曲線時間軸" $axisOkay "點數 $($referenceAxis.Count)"

    $reportItems = @($result.strategies) + @($result.buy_and_hold)
    foreach ($item in $reportItems) {
        $summary = $item.summary
        $curveFinal = [double]$item.equity_curve[-1].equity
        $initial = [double]$summary.initial_capital
        $mathOkay = [Math]::Abs([double]$summary.final_equity - $curveFinal) -lt 0.01 -and
            [Math]::Abs([double]$summary.net_pnl - ($curveFinal - $initial)) -lt 0.01 -and
            [Math]::Abs([double]$summary.total_return_pct - (($curveFinal / $initial - 1) * 100)) -lt 0.00001
        Write-Check "$($item.strategy_id) 摘要與資金曲線一致" $mathOkay
    }

    Write-Host "結果資料夾：$($result.audit.output_directory)"
    Write-Host "策略結果："
    foreach ($item in $result.strategies) {
        Write-Host ("  {0}: Return {1:N3}% | Trades {2} | MaxDD {3:N3}%" -f $item.strategy_id, $item.summary.total_return_pct, $item.summary.trade_count, $item.summary.max_drawdown_pct)
    }
    Write-Host ("  buy_and_hold: Return {0:N3}% | Shares {1} | Costs {2:N2}" -f $result.buy_and_hold.summary.total_return_pct, $result.buy_and_hold.details.shares_held, $result.buy_and_hold.details.total_transaction_cost)
} catch {
    $script:FatalError = $_.Exception.Message
    Write-Host "驗收中止：$($_.Exception.Message)" -ForegroundColor Yellow
} finally {
    $token = $null
    $password = $null
    if ($passwordBstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordBstr) }
}

if ($script:FatalError) { $script:FailCount++ }
Write-Host "PASS=$script:PassCount FAIL=$script:FailCount"
if ($script:FailCount -gt 0) { exit 1 }
