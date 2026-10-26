// Uses pywebview's real API generator under the page CSP. Only native transport is simulated.
const fs=require('fs'),http=require('http'),path=require('path'),cp=require('child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../ui');
let bridge;
if(process.env.BRIDGE_API_PATH)bridge=fs.readFileSync(process.env.BRIDGE_API_PATH,'utf8');
else {
 const location=cp.execFileSync('python',['-c',"import importlib.util; from pathlib import Path; print(Path(importlib.util.find_spec('webview').origin).parent / 'js' / 'api.js')"],{encoding:'utf8'}).trim();
 bridge=fs.readFileSync(location,'utf8');
}
bridge=bridge.replaceAll('%(token)s','test-token').replaceAll('%(platform)s','edgechromium');
const functions=['get_settings','startup','play','get_launch_state'].map(func=>({func,params:[]}));
const transport=`
window.bridgeCalls=[];
window.chrome=window.chrome||{};
window.chrome.webview={postMessage:([name,raw,id])=>{
 bridgeCalls.push([name,JSON.parse(raw)]);
 let value={ok:true};
 if(name==='get_settings')value={version:'bridge-test',profile:{name:'Steve',connected:false},ram_mb:4096,resolution_width:1280,resolution_height:720,auth_api_url:''};
 if(name==='get_launch_state')value={kind:'idle',sequence:0,busy:false};
 setTimeout(()=>{
  pywebview._returnValuesCallbacks[name][id]({value:JSON.stringify(value),isError:false});
  if(name==='startup')receiveLauncherEvent({kind:'main'});
 },0);
}};
try{pywebview._createApi(${JSON.stringify(functions)});window.dispatchEvent(new Event('pywebviewready'));}
catch(e){window.bridgeFailure=e.message;}
`;
(async()=>{
 const server=http.createServer((req,res)=>{
  if(req.url==='/native-bridge-test.js'){res.setHeader('Content-Type','text/javascript');res.end(bridge+'\n'+transport);return;}
  const url=new URL(req.url,'http://localhost');
  const file=path.resolve(root,'.'+url.pathname);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
  res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.ttf':'font/ttf','.ogg':'audio/ogg'})[path.extname(file)]||'application/octet-stream');
  if(url.pathname==='/index.html'){
   let html=fs.readFileSync(file,'utf8');
   if(url.searchParams.has('blocked'))html=html.replace(" 'unsafe-eval'",'');
   html=html.replace('</body>','<script src="native-bridge-test.js"></script></body>');
   res.end(html);
  }else fs.createReadStream(file).pipe(res);
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1180,height:664}});
  const base='http://127.0.0.1:'+server.address().port;
  await page.goto(base+'/index.html?blocked=1');
  await page.waitForFunction(()=>!!window.bridgeFailure);
  const failure=await page.evaluate(()=>window.bridgeFailure);
  if(!/unsafe-eval|Content Security Policy|EvalError/i.test(failure))throw Error('Unexpected old-policy failure: '+failure);
  console.log('PASS: reproduced old CSP blocking real pywebview API creation');
  await page.goto(base+'/index.html');
  await page.waitForFunction(()=>!document.getElementById('app').hidden&&window.bridgeCalls.some(c=>c[0]==='startup'));
  if(await page.evaluate(()=>window.bridgeFailure))throw Error('Native API generation still blocked');
  await page.click('#play');
  await page.waitForFunction(()=>window.bridgeCalls.some(c=>c[0]==='play'&&c[1][0]===false));
  console.log('PASS: real pywebview API generator, initialization and Play transport work with production CSP');
 }finally{if(browser)await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exit(1);});
