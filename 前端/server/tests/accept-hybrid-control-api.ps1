param(
    [string]$BaseUrl = "http://localhost:3000",
    [switch]$SelfTestOnly
)

$ErrorActionPreference = "Stop"
$script:FailureCount = 0
$BaseUrl = $BaseUrl.TrimEnd('/')

function Write-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail = ""
    )

    if ($Passed) {
        Write-Host "PASS  $Name" -ForegroundColor Green
    }
    else {
        $script:FailureCount += 1
        if ($Detail) {
            Write-Host "FAIL  $Name - $Detail" -ForegroundColor Red
        }
        else {
            Write-Host "FAIL  $Name" -ForegroundColor Red
        }
    }
}

function Convert-ResponseJson {
    param([string]$Content)
    if ([string]::IsNullOrWhiteSpace($Content)) { return $null }
    try { return $Content | ConvertFrom-Json -ErrorAction Stop }
    catch { return $null }
}

function Get-HttpErrorResponseBody {
    param($ErrorRecord)

    $detailsMessage = [string]$ErrorRecord.ErrorDetails.Message
    if (-not [string]::IsNullOrWhiteSpace($detailsMessage)) {
        # Windows PowerShell 5.1 may preserve the non-2xx body in ErrorDetails.
        # Prefer it when it contains valid JSON; otherwise try the response stream.
        if ($null -ne (Convert-ResponseJson $detailsMessage)) { return $detailsMessage }
    }

    $webResponse = $ErrorRecord.Exception.Response
    if ($null -eq $webResponse) { return "" }

    $reader = $null
    try {
        $responseStream = $webResponse.GetResponseStream()
        if ($null -eq $responseStream) { return "" }
        $reader = New-Object System.IO.StreamReader($responseStream)
        return $reader.ReadToEnd()
    }
    catch { return "" }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
    }
}

function Test-HttpErrorResponseParsing {
    $expectedCode = "RESEARCH_RESULT_NOT_FOUND"
    $detailsJson = '{"error":{"code":"RESEARCH_RESULT_NOT_FOUND","message":"not found"}}'

    $detailsRecord = [pscustomobject]@{
        ErrorDetails = [pscustomobject]@{ Message = $detailsJson }
        Exception = [pscustomobject]@{ Response = $null }
    }
    $detailsBody = Get-HttpErrorResponseBody $detailsRecord
    $detailsData = Convert-ResponseJson $detailsBody
    $detailsPassed = $null -ne $detailsData -and $detailsData.error.code -eq $expectedCode
    Write-Check -Name "模擬 HTTP 404：優先解析 ErrorDetails JSON error.code" -Passed $detailsPassed

    $fallbackJson = '{"error":{"code":"RESEARCH_RESULT_NOT_FOUND","message":"not found"}}'
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($fallbackJson)
    $stream = New-Object System.IO.MemoryStream(,$bytes)
    $mockResponse = [pscustomobject]@{ BodyStream = $stream }
    $mockResponse | Add-Member -MemberType ScriptMethod -Name GetResponseStream -Value { return $this.BodyStream }
    $fallbackRecord = [pscustomobject]@{
        ErrorDetails = [pscustomobject]@{ Message = "" }
        Exception = [pscustomobject]@{ Response = $mockResponse }
    }
    $fallbackBody = Get-HttpErrorResponseBody $fallbackRecord
    $fallbackData = Convert-ResponseJson $fallbackBody
    $fallbackPassed = $null -ne $fallbackData -and $fallbackData.error.code -eq $expectedCode
    Write-Check -Name "模擬 HTTP 404：ErrorDetails 空白時讀取 Response Stream" -Passed $fallbackPassed
    $stream.Dispose()
}

function Invoke-LocalApi {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [string]$Method = "GET",
        [hashtable]$Headers = @{},
        [string]$Body
    )

    $request = @{
        Uri = $Uri
        Method = $Method
        Headers = $Headers
        UseBasicParsing = $true
        ErrorAction = "Stop"
    }
    if ($PSBoundParameters.ContainsKey("Body")) {
        $request.ContentType = "application/json"
        $request.Body = $Body
    }

    try {
        $response = Invoke-WebRequest @request
        return @{
            StatusCode = [int]$response.StatusCode
            Data = Convert-ResponseJson ([string]$response.Content)
        }
    }
    catch {
        $httpError = $_
        $webResponse = $httpError.Exception.Response
        if ($null -eq $webResponse) {
            return @{ StatusCode = 0; Data = $null }
        }

        $statusCode = 0
        try { $statusCode = [int]$webResponse.StatusCode } catch { }
        $content = Get-HttpErrorResponseBody $httpError

        return @{
            StatusCode = $statusCode
            Data = Convert-ResponseJson $content
        }
    }
}

if ($SelfTestOnly) {
    Write-Host "HTTP 錯誤本文解析自我測試（不登入、不連線）"
    Test-HttpErrorResponseParsing
    if ($script:FailureCount -gt 0) {
        Write-Host "結果：$script:FailureCount 項 FAIL。"
        exit 1
    }
    Write-Host "結果：錯誤本文解析測試全部 PASS。"
    exit 0
}
Write-Host "Hybrid Control + RF Gate + Risk V1 本機 HTTP 驗收"
Write-Host "API：$BaseUrl"

$hybridUri = "$BaseUrl/api/research/v2/hybrid/2330"
$unauthenticated = Invoke-LocalApi -Uri $hybridUri
Write-Check -Name "未登入請求回傳 HTTP 401" -Passed ($unauthenticated.StatusCode -eq 401) -Detail "HTTP $($unauthenticated.StatusCode)"

$email = Read-Host "請輸入現有帳號電子郵件"
$securePassword = Read-Host "請輸入密碼（輸入時不會顯示）" -AsSecureString
$passwordPointer = [IntPtr]::Zero
$passwordText = $null
$jwt = $null

try {
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    $passwordText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    $loginBody = @{ email = $email; password = $passwordText } | ConvertTo-Json -Compress
    $login = Invoke-LocalApi -Uri "$BaseUrl/api/auth/login" -Method "POST" -Body $loginBody
    $passwordText = $null
    $loginBody = $null
}
finally {
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    $securePassword = $null
}

if ($login.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace([string]$login.Data.token)) {
    $loginReason = switch ($login.StatusCode) {
        0 { "無法連線到 API；請確認 Express 正在 localhost:3000 執行。" }
        400 { "登入 API 未接受請求，請確認電子郵件與密碼欄位。" }
        401 { "帳號或密碼未通過驗證。" }
        404 { "找不到此帳號。" }
        default { "登入失敗（HTTP $($login.StatusCode)）；請檢查 API 狀態。" }
    }
    Write-Check -Name "使用現有帳號登入並取得 JWT" -Passed $false -Detail $loginReason
    Write-Host "測試中止；沒有輸出帳號、密碼或 Token。"
    exit 1
}

$jwt = [string]$login.Data.token
$login = $null
Write-Check -Name "登入成功並在本次程序記憶體取得 JWT" -Passed $true

try {
    $authHeaders = @{ Authorization = "Bearer $jwt" }
    $result = Invoke-LocalApi -Uri $hybridUri -Headers $authHeaders
    Write-Check -Name "Hybrid 2330 查詢回傳 HTTP 200" -Passed ($result.StatusCode -eq 200) -Detail "HTTP $($result.StatusCode)"

    if ($result.StatusCode -ne 200 -or $null -eq $result.Data) {
        Write-Host "未取得研究 JSON；請提供 HTTP 狀態碼及伺服器的非機密錯誤訊息。"
        exit 1
    }

    $data = $result.Data
    Write-Check -Name "research_variant = CONTROL" -Passed ($data.research_variant -eq "CONTROL")
    Write-Check -Name "strategy_version 為 Control + RF BUY-only Gate + Risk V1" -Passed ($data.strategy_version -eq "Technical Control + ML V1 RF BUY-only Gate + Risk V1")
    Write-Check -Name "未套用 V2-B 市場狀態過濾" -Passed ($data.technical_source.market_regime_filter.applied -eq $false)
    Write-Check -Name "2330 Common Coverage 候選數 = 47" -Passed ([int]$data.gate.decisions.candidate_count -eq 47)
    Write-Check -Name "RF Gate ENTRY = 14" -Passed ([int]$data.gate.decisions.entry -eq 14)
    Write-Check -Name "RF Gate BLOCK_HOLD = 16" -Passed ([int]$data.gate.decisions.block_hold -eq 16)
    Write-Check -Name "RF Gate BLOCK_SELL = 17" -Passed ([int]$data.gate.decisions.block_sell -eq 17)
    Write-Check -Name "Risk sizing 決策數 = 14" -Passed ([int]$data.risk.risk_decision_count -eq 14)
    Write-Check -Name "Ledger 列數 = 28" -Passed (@($data.transactions).Count -eq 28)
    Write-Check -Name "實際交易數 = 14" -Passed ([int]$data.performance.stock_summary.trade_count -eq 14)
    $returnOk = $null -ne $data.performance.stock_summary.average_fold_return_pct -and [Math]::Abs([double]$data.performance.stock_summary.average_fold_return_pct - 1.761104925) -le 0.000001
    Write-Check -Name "平均 Fold 報酬約為 1.761104925%" -Passed $returnOk

    $foldTrades = @($data.performance.per_fold | ForEach-Object { [int]$_.metrics.trade_count })
    $foldTradesOk = $foldTrades.Count -eq 3 -and $foldTrades[0] -eq 0 -and $foldTrades[1] -eq 9 -and $foldTrades[2] -eq 5
    Write-Check -Name "Fold 交易數為 [0, 9, 5]" -Passed $foldTradesOk -Detail ($foldTrades -join ", ")
    Write-Check -Name "cumulative_return_pct 為 null" -Passed ($null -eq $data.performance.stock_summary.cumulative_return_pct)

    Write-Check -Name "Technical experiment ID 符合來源稽核" -Passed ($data.technical_experiment_id -eq "technical-v1-20261010-v1")
    Write-Check -Name "Common Coverage experiment ID 符合來源稽核" -Passed ($data.common_coverage_experiment_id -eq "hybrid-common-coverage-20261018T121416162413")
    Write-Check -Name "Risk ID / experiment ID 符合來源稽核" -Passed ($data.risk_id -eq "risk_v1" -and $data.risk_experiment_id -eq "hybrid-risk-v1-20261018T122032419364")

    $missingStock = Invoke-LocalApi -Uri "$BaseUrl/api/research/v2/hybrid/9999" -Headers $authHeaders
    $missingCode = if ($missingStock.Data.error.code) { [string]$missingStock.Data.error.code } else { "" }
    Write-Check -Name "不存在的股票回傳 HTTP 404" -Passed ($missingStock.StatusCode -eq 404) -Detail "HTTP $($missingStock.StatusCode)"
    Write-Check -Name "不存在股票錯誤碼為 RESEARCH_RESULT_NOT_FOUND" -Passed ($missingCode -eq "RESEARCH_RESULT_NOT_FOUND")

    Write-Host ""
    Write-Host "摘要：HTTP 200；Control；候選 47；ENTRY 14；交易 14；平均 Fold 報酬 $([Math]::Round([double]$data.performance.stock_summary.average_fold_return_pct, 9))%。"
    if ($script:FailureCount -eq 0) {
        Write-Host "結果：全部驗收項目 PASS。"
    }
    else {
        Write-Host "結果：$script:FailureCount 項 FAIL，請查看上方項目。"
    }
}
finally {
    $authHeaders = $null
    $jwt = $null
    $data = $null
    $result = $null
    $missingStock = $null
}

if ($script:FailureCount -gt 0) { exit 1 }
exit 0
