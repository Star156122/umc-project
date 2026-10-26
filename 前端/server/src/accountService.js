const DEFAULTS = Object.freeze({stock:'2330',strategies:['technical','rf','xgboost','gru'],showCharts:true,expandDetails:false,completionNotice:true,browserNotice:false});
function validateSettings(value){
 if(!value||typeof value!=='object'||Array.isArray(value))throw Error('設定格式不正確');
 const s={...DEFAULTS,...value};
 if(!['2303','2330','2379','2454','3034'].includes(s.stock))throw Error('請選擇支援的股票');
 if(!Array.isArray(s.strategies)||!s.strategies.length||s.strategies.some(x=>!DEFAULTS.strategies.includes(x)))throw Error('請至少選擇一種有效策略');
 const out={stock:s.stock,strategies:[...new Set(s.strategies)]};
 for(const k of ['showCharts','expandDetails','completionNotice','browserNotice']){if(typeof s[k]!=='boolean')throw Error('設定值必須為開啟或關閉');out[k]=s[k];}
 return out;
}
function createAccountService(pool){
 let ready;
 async function ensure(){if(!ready)ready=pool.execute(`CREATE TABLE IF NOT EXISTS user_app_settings (user_id INT NOT NULL PRIMARY KEY, settings_json TEXT NULL, session_version INT NOT NULL DEFAULT 0)`).catch(e=>{ready=null;throw e;});await ready;}
 async function read(id){await ensure();const [rows]=await pool.execute('SELECT settings_json, session_version FROM user_app_settings WHERE user_id = ?',[id]);return {settings:rows[0]?.settings_json?validateSettings(JSON.parse(rows[0].settings_json)):{...DEFAULTS},version:Number(rows[0]?.session_version||0)};}
 async function save(id,input){const settings=validateSettings(input);await ensure();await pool.execute('INSERT INTO user_app_settings (user_id, settings_json) VALUES (?, ?) ON DUPLICATE KEY UPDATE settings_json = VALUES(settings_json)',[id,JSON.stringify(settings)]);return settings;}
 async function revoke(id){await ensure();await pool.execute('INSERT INTO user_app_settings (user_id, session_version) VALUES (?, 1) ON DUPLICATE KEY UPDATE session_version = session_version + 1',[id]);}
 return {read,save,revoke,ensure};
}
function createAccountRouter({express,pool,authenticate,service,hashPassword,verifyPassword}){
 const router=express.Router();router.use(authenticate);
 const attempts=new Map();
 const wrap=fn=>async(req,res)=>{try{await fn(req,res);}catch(e){res.status(500).json({message:'帳號服務暫時無法使用，請確認資料庫連線後重試'});}};
 router.get('/',wrap(async(req,res)=>{const [rows]=await pool.execute('SELECT user_id, username, email, created_at, last_login FROM users WHERE user_id = ?',[req.user.userId]);if(!rows[0])return res.status(401).json({message:'帳號不存在，請重新登入'});res.json({user:rows[0],...(await service.read(req.user.userId))});}));
 router.put('/settings',wrap(async(req,res)=>{let value;try{value=validateSettings(req.body);}catch(e){return res.status(400).json({message:e.message});}res.json({settings:await service.save(req.user.userId,value),message:'設定已儲存'});}));
 router.post('/logout-all',wrap(async(req,res)=>{await service.revoke(req.user.userId);res.json({message:'所有登入憑證已失效，請重新登入'});}));
 router.post('/password',wrap(async(req,res)=>{
  const id=req.user.userId,now=Date.now();for(const [k,v] of attempts)if(v.until<now)attempts.delete(k);
  const trial=attempts.get(id)||{count:0,until:now+15*60*1000};if(trial.count>=5)return res.status(429).json({message:'嘗試次數過多，請 15 分鐘後再試'});
  const {currentPassword,newPassword}=req.body||{};
  if(typeof currentPassword!=='string'||typeof newPassword!=='string'||newPassword.length<8||newPassword.length>128||currentPassword.length>128)return res.status(400).json({message:'新密碼須為 8～128 個字元'});
  if(currentPassword===newPassword)return res.status(400).json({message:'新密碼不可與原密碼相同'});
  const [rows]=await pool.execute('SELECT password_hash FROM users WHERE user_id = ?',[id]);
  if(!rows[0]||!verifyPassword(currentPassword,rows[0].password_hash)){trial.count++;attempts.set(id,trial);return res.status(400).json({message:'目前密碼不正確'});}
  await service.ensure();const connection=await pool.getConnection();
  try{await connection.beginTransaction();const [changed]=await connection.execute('UPDATE users SET password_hash = ? WHERE user_id = ? AND password_hash = ?',[hashPassword(newPassword),id,rows[0].password_hash]);if(changed.affectedRows!==1)throw Error('password changed concurrently');await connection.execute('INSERT INTO user_app_settings (user_id, session_version) VALUES (?, 1) ON DUPLICATE KEY UPDATE session_version = session_version + 1',[id]);await connection.commit();}catch(e){await connection.rollback();throw e;}finally{connection.release();}
  attempts.delete(id);res.json({message:'密碼已更新，所有裝置需重新登入'});
 }));
 router.get('/export',wrap(async(req,res)=>{
  const id=req.user.userId;
  const [users]=await pool.execute('SELECT username, email, created_at, last_login FROM users WHERE user_id = ?',[id]);
  const [favorites]=await pool.execute('SELECT stock_id, added_at FROM favorite_stocks WHERE user_id = ?',[id]);
  res.json({exportedAt:new Date().toISOString(),account:users[0],favorites,settings:(await service.read(id)).settings,scope:'帳號基本資料、自選股及個人設定；不包含密碼、登入憑證或回測報告'});
 }));
 return router;
}
module.exports={DEFAULTS,validateSettings,createAccountService,createAccountRouter};
