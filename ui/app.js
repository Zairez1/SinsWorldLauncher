'use strict';
const $=id=>document.getElementById(id);
let selectedServer='sinsworld';
let settings={},profile={},authMode='login',busy=false,toastTimer=0,newsLoaded=false,nativeReady=false;
async function api(name,...args){if(!nativeReady)throw Error('Это браузерный просмотр. Запустите launcher.py для рабочих действий.');return window.pywebview.api[name](...args);}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
function page(name){$('app-stage').classList.toggle('is-home',name==='home');document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===name));document.querySelectorAll('[data-page]').forEach(x=>x.classList.toggle('active',x.dataset.page===name));updateNavIndicator();if(name==='account')$('profile-button').setAttribute('aria-current','page');else $('profile-button').removeAttribute('aria-current');$('recovery-result').hidden=true;if(name==='news'&&!newsLoaded)loadNews();}
function setProfile(value){profile=value||{};$('account').dataset.connected=String(!!profile.connected);$('profile-name').textContent=profile.name||'Аккаунт';$('home-profile').textContent=profile.name?(profile.connected?'● В сети · ':'● Локальный профиль · ')+profile.name:'● Не авторизован';$('signed-in').hidden=!profile.connected;$('account-forms').hidden=!!profile.connected;$('signed-name').textContent=profile.name||'';drawProfileHead(profile.skin_data);if(profile.connected)$('account-title').innerHTML='Мой <span>аккаунт</span>';if(!profile.connected){$('skin-file-name').textContent='Файл не выбран';$('skin-preview').hidden=true;$('skin-preview').removeAttribute('src');$('skin-result').textContent='';}requestAvatar();}
let avatarDrawSequence=0;
function drawProfileHead(data){
 const seq=++avatarDrawSequence,canvas=$('profile-head'),holder=canvas.parentElement;
 canvas.hidden=true;holder.classList.remove('has-skin');
 if(typeof data!=='string'||!/^data:image\/png;base64,/.test(data)||data.length>350000)return;
 const image=new Image();image.onload=()=>{
  if(seq!==avatarDrawSequence||image.width!==64||![32,64].includes(image.height))return;
  const context=canvas.getContext('2d');context.clearRect(0,0,32,32);context.imageSmoothingEnabled=false;
  context.drawImage(image,8,8,8,8,0,0,32,32);
  const layer=document.createElement('canvas');layer.width=layer.height=8;
  const lc=layer.getContext('2d');lc.drawImage(image,40,8,8,8,0,0,8,8);
  // Legacy opaque hat areas are unused skin background, as in Minecraft's legacy skin conversion.
  const alpha=lc.getImageData(0,0,8,8).data;
  if(image.height!==32||alpha.some((v,i)=>i%4===3&&v<255))context.drawImage(layer,0,0,8,8,0,0,32,32);
  canvas.hidden=false;holder.classList.add('has-skin');
 };image.src=data;
}
function requestAvatar(){if(nativeReady&&profile.connected&&profile.id)api('refresh_avatar').catch(()=>{});}
setInterval(()=>{if(!document.hidden)requestAvatar();},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)requestAvatar();});
function setBusy(value){busy=value;$('skin-save').disabled=value;$('skin-pick').disabled=value;$('server-selector').querySelector('summary').setAttribute('aria-disabled',String(value));$('server-sinsworld').disabled=value;if(value)$('server-selector').open=false;for(const id of ['play','sync-mods'])$(id).disabled=value||selectedServer!=='sinsworld';$('server-naruto').disabled=value;$('server-solo').disabled=value;$('play').setAttribute('aria-busy',String(value));}
function splash(){return $('splash-frame').contentWindow.LauncherUpdaterDemo;}
window.closeLauncher=()=>api('close').then(r=>{if(r&&!r.ok)toast(r.error);}).catch(e=>toast(e.message));
window.receiveLauncherEvent=function(event){
 if(event.kind==='skin_change'){$('skin-result').textContent=event.message;const pending=event.state==='pending';$('skin-save').disabled=pending;$('skin-pick').disabled=pending;setBusy(pending);return;}
 if(event.kind==='avatar'){if(profile.connected&&profile.id===event.account_id){profile.skin_data=event.skin_data;drawProfileHead(event.skin_data);}return;}
 if(event.kind==='update_required'){$('required-update').hidden=false;$('required-update-message').textContent=event.message+' Версия '+event.version+'.';$('download-new').disabled=false;$('download-new').focus();return;}
 if(!$('required-update').hidden&&event.kind==='error'){$('download-new').disabled=false;$('required-update-status').textContent=event.message;return;}
 if(!$('required-update').hidden&&event.kind==='splash_progress'){$('required-update-status').textContent='Загрузка: '+Math.round(event.checked/event.total*100)+'%';}
 if(!$('required-update').hidden&&event.kind==='splash'&&event.state==='installing')$('required-update-status').textContent='Установка и перезапуск…';
 if(event.kind==='splash'){try{splash().setState(event.state);if(event.progress!==undefined&&event.state==='no-update')splash().setState('no-update');}catch{}return;}
 if(event.kind==='splash_progress'){try{splash().setProgress({checked:event.checked,total:event.total});}catch{}return;}
 if(event.kind==='main'){$('startup').hidden=true;$('app').hidden=false;musicTryPlay();return;}
 if(['configuration','source_update'].includes(event.kind)){$('startup-message').hidden=false;$('startup-text').textContent=event.message;return;}
 if(event.kind==='error'){setBusy(false);$('operation-progress').hidden=true;if(event.startup){try{splash().setState('error');}catch{}$('startup-message').hidden=false;$('startup-text').textContent=event.message;}else{toast(event.message);$('operation-text').textContent=event.message;}return;}
 if(event.kind==='idle'){setBusy(false);return;}
 if(event.kind==='profile'){setProfile(event.profile);return;}
 if(event.kind==='notice'){toast(event.message);return;}
 if(event.kind==='game_progress'){setBusy(true);$('operation-text').textContent=/^Актуален:|^Проверен:/.test(event.message)?'Проверка модов':/^Загрузка:/.test(event.message)?'Обновление модов':event.message;const known=Number.isFinite(event.checked)&&Number.isFinite(event.total)&&event.total>0;$('operation-progress').hidden=!known;if(known)$('operation-fill').style.width=Math.max(0,Math.min(100,event.checked/event.total*100))+'%';return;}
 if(event.kind==='game_exited'){requestAvatar();musicGameRunning=false;musicTryPlay();$('operation-text').textContent=event.message;return;}
 if(event.kind==='game_started'){musicGameRunning=true;$('background-music').pause();$('operation-text').textContent=event.message;$('operation-progress').hidden=true;toast(event.message);}
};
function applySettings(value){settings=value;applyServer(value.selected_server||'sinsworld');const form=$('settings-form');for(const [key,v]of Object.entries(value)){const input=form.elements.namedItem(key);if(!input)continue;if(input.type==='checkbox')input.checked=!!v;else input.value=v??'';}$('version').textContent='Лаунчер · v'+(value.version||'preview');setProfile(value.profile);$('local-account').hidden=!!value.auth_api_url;setupVideo(value.trailer_url);updateRam();$('game-directory').textContent=value.game_directory||'Папка игры';const res=String(value.resolution_width)+'x'+String(value.resolution_height);if(![...$('resolution-preset').options].some(o=>o.value===res))$('resolution-preset').add(new Option(res.replace('x',' × '),res));$('resolution-preset').value=res;auth(authMode);}
function setupVideo(url){if(selectedServer!=='sinsworld')url='';const video=$('trailer');video.pause();video.classList.remove('ready');video.removeAttribute('src');$('video-toggle').hidden=true;if(!url||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;try{if(new URL(url).protocol!=='https:')return;}catch{return;}video.muted=true;video.src=url;video.onloadeddata=()=>{video.play().then(()=>{video.classList.add('ready');$('video-toggle').hidden=false;}).catch(()=>{});};video.onerror=()=>{video.classList.remove('ready');$('video-toggle').hidden=true;};video.load();}
document.addEventListener('visibilitychange',()=>{const v=$('trailer');if(document.hidden)v.pause();else if(v.classList.contains('ready')&&$('video-toggle').textContent==='Пауза видео')v.play().catch(()=>{});});
$('video-toggle').onclick=()=>{const v=$('trailer');if(v.paused){v.play().then(()=>$('video-toggle').textContent='Пауза видео').catch(()=>toast('Видео недоступно'));}else{v.pause();$('video-toggle').textContent='Включить видео';}};
document.querySelectorAll('[data-page]').forEach(x=>x.onclick=()=>page(x.dataset.page));$('profile-button').onclick=()=>page('account');$('close').onclick=window.closeLauncher;$('minimize').onclick=()=>api('minimize').catch(e=>toast(e.message));
async function launch(onlyMods){if(selectedServer!=='sinsworld'){toast('Сервер '+(selectedServer==='solo'?'Solo Leveling':'Наруто')+' готовится к открытию');return;}try{setBusy(true);$('operation-text').textContent='Подготовка к запуску…';$('play').setAttribute('aria-busy','true');const result=await api('play',onlyMods);if(!result.ok){setBusy(false);toast(result.error);$('operation-text').textContent=result.error;if(!profile.name)page('account');}}catch(e){setBusy(false);toast(e.message);}}
$('play').onclick=()=>launch(false);$('sync-mods').onclick=()=>launch(true);
$('settings-form').onsubmit=async event=>{event.preventDefault();if(busy){toast('Дождитесь завершения операции');return;}const values={};for(const input of event.target.elements){if(input.name)values[input.name]=input.type==='checkbox'?input.checked:input.value;}try{const result=await api('save_settings',values);if(!result.ok)throw Error(result.error);applySettings(result.settings);newsLoaded=false;$('settings-result').textContent='Настройки сохранены';}catch(e){$('settings-result').textContent=e.message;}};
$('game-folder').onclick=()=>api('open_game_folder').catch(e=>toast(e.message));$('startup-continue').onclick=async()=>{try{const r=await api('continue_to_main');if(!r.ok)throw Error(r.error);page('settings');}catch(e){toast(e.message);}};$('startup-retry').onclick=async()=>{try{$('startup-message').hidden=true;const r=await api('check_launcher');if(!r.ok)throw Error(r.error);}catch(e){toast(e.message);}};
async function loadNews(){$('news-list').textContent='Загрузка…';try{const r=await api('get_news');if(!r.ok)throw Error(r.error);$('news-list').replaceChildren();if(!r.items.length)$('news-list').textContent='Новостей пока нет.';for(const item of r.items){const a=document.createElement('article');a.className='news-item';const date=document.createElement('time'),title=document.createElement('h3'),body=document.createElement('p');date.textContent=item.date;title.textContent=item.title;body.textContent=item.body;a.append(date,title,body);$('news-list').append(a);}newsLoaded=true;}catch(e){$('news-list').textContent=e.message;}}$('refresh-news').onclick=loadNews;
function auth(mode){authMode=mode;const authme=settings.auth_mode==='authme_bridge';$('password').minLength=authme&&mode==='login'?5:12;$('password').maxLength=authme?30:128;$('repeat').maxLength=authme?30:128;$('account').dataset.mode=mode;$('account-title').innerHTML=profile.connected?'Мой <span>аккаунт</span>':mode==='login'?'Вход в <span>аккаунт</span>':mode==='register'?'Создать <span>аккаунт</span>':'Восстановить <span>доступ</span>';document.querySelectorAll('[data-auth]').forEach(x=>x.hidden=x.dataset.auth===mode||mode==='register'&&x.dataset.auth==='recover');$('register-hint').hidden=mode!=='register';document.querySelectorAll('[data-auth]').forEach(x=>x.classList.toggle('selected',x.dataset.auth===mode));$('repeat-label').hidden=mode==='login';$('repeat').required=mode!=='login';$('recovery-label').hidden=mode!=='recover';$('recovery').required=mode==='recover';$('remember-label').hidden=mode!=='login';$('account-submit').textContent=mode==='login'?'Войти':mode==='register'?'Создать аккаунт':'Восстановить доступ';$('password').autocomplete=mode==='login'?'current-password':'new-password';$('account-result').textContent='';$('recovery-result').hidden=true;}
document.querySelectorAll('[data-auth]').forEach(x=>x.onclick=()=>auth(x.dataset.auth));
$('account-form').onsubmit=async e=>{e.preventDefault();$('account-submit').disabled=true;try{const r=await api('account',authMode,$('nickname').value.trim(),$('password').value,$('repeat').value,$('remember').checked,$('recovery').value.trim());if(!r.ok)throw Error(r.error);$('password').value='';$('repeat').value='';$('recovery').value='';if(r.profile)setProfile(r.profile);$('account-result').textContent=r.warning||r.message||'Вход выполнен';if(r.recovery_code){$('recovery-code').textContent=r.recovery_code;$('recovery-result').hidden=false;}}catch(e){$('account-result').textContent=e.message;}finally{$('account-submit').disabled=false;}};
$('local-profile').onclick=async()=>{try{const r=await api('account','local',$('nickname').value.trim());if(!r.ok)throw Error(r.error);setProfile(r.profile);toast('Локальный профиль сохранён');page('home');}catch(e){$('account-result').textContent=e.message;}};
$('logout').onclick=async()=>{try{const r=await api('logout');setProfile(r.profile);$('recovery-code').textContent='';$('recovery-result').hidden=true;}catch(e){toast(e.message);}};
async function initialize(){try{nativeReady=true;applySettings(await api('get_settings'));await new Promise(resolve=>{if($('splash-frame').contentDocument.readyState==='complete')resolve();else $('splash-frame').addEventListener('load',resolve,{once:true});});splash().setTheme(selectedServer);await splash().open();await api('startup');}catch(e){toast(e.message);}}
window.addEventListener('pywebviewready',initialize);
setTimeout(()=>{if(!nativeReady){$('startup').hidden=true;$('app').hidden=false;applySettings({version:'preview',profile:{name:'',connected:false},ram_mb:6144,resolution_width:1280,resolution_height:720,launcher_release:'launcher-current',mods_release:'mods-current'});toast('Браузерный просмотр. Рабочие функции доступны при запуске launcher.py.');}},1800);


function fitArtboard(){const scale=Math.min(innerWidth/1672,innerHeight/941);const stage=$('app-stage');stage.style.transform='translate(-50%,-50%) scale('+scale+')';}
window.addEventListener('resize',fitArtboard);fitArtboard();

function updateRam(){const input=$('settings-form').elements.namedItem('ram_mb');$('ram-value').textContent=(Number(input.value)/1024).toLocaleString('ru-RU')+' ГБ';input.style.setProperty('--range',((input.value-input.min)/(input.max-input.min)*100)+'%');}
$('settings-form').elements.namedItem('ram_mb').oninput=updateRam;
$('resolution-preset').onchange=e=>{const [w,h]=e.target.value.split('x');$('settings-form').elements.namedItem('resolution_width').value=w;$('settings-form').elements.namedItem('resolution_height').value=h;};
document.querySelectorAll('[data-settings]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-settings]').forEach(x=>x.classList.toggle('selected',x===b));document.querySelectorAll('[data-settings-section]').forEach(x=>x.classList.toggle('active',x.dataset.settingsSection===b.dataset.settings));});
document.querySelectorAll('[data-password]').forEach(b=>b.onclick=()=>{const input=$(b.dataset.password);const show=input.type==='password';input.type=show?'text':'password';b.setAttribute('aria-label',show?'Скрыть пароль':'Показать пароль');});
$('maximize').onclick=()=>api('toggle_maximize').catch(e=>toast(e.message));
auth('login');

const serverMusic={
 sinsworld:[['peaceful_forest.ogg','Peaceful Forest'],['sunset_plains.ogg','Sunset Plains'],['orchestral_epic_fantasy.ogg','Orchestral Epic Fantasy']],
 naruto:[['naruto-eastern-thought.ogg','Eastern Thought'],['naruto-asian-drums.ogg','Asian Drums']],
 solo:[["solo-ost-01.ogg", "DARK ARIA (Vocal: XAI)"], ["solo-ost-02.ogg", "SymphonicSuite-Lv.1"], ["solo-ost-04.ogg", "SymphonicSuite-Lv.3"], ["solo-ost-05.ogg", "SymphonicSuite-Lv.4"], ["solo-ost-06.ogg", "SymphonicSuite-Lv.5"], ["solo-ost-07.ogg", "SymphonicSuite-Lv.6"], ["solo-ost-08.ogg", "SymphonicSuite-Lv.7"], ["solo-ost-09.ogg", "SymphonicSuite-Lv.8 (Vocal: Hannah Grace)"], ["solo-ost-10.ogg", "SymphonicSuite-Lv.9"], ["solo-ost-11.ogg", "SymphonicSuite-Lv.10"], ["solo-ost-12.ogg", "DunGeoN"], ["solo-ost-13.ogg", "KSK→GATE"], ["solo-ost-14.ogg", "Hunter→Monster"], ["solo-ost-15.ogg", "Am→Km (Vocal: XAI)"], ["solo-ost-16.ogg", "everydayLV.0"], ["solo-ost-17.ogg", "aikari"], ["solo-ost-18.ogg", "onlyORE"], ["solo-ost-19.ogg", "4eVR (Vocal: Benjamin & mpi, Laco)"]]
};
let musicTracks=serverMusic.sinsworld,musicServer='sinsworld';
let musicIndex=0,musicGameRunning=false,musicEnabled=localStorage.getItem('sinsworld.music.enabled')!=='false';
const music=$('background-music');music.src=musicTracks[0][0];
let savedMusicVolume=Number(localStorage.getItem('sinsworld.music.volume')??20);if(!Number.isFinite(savedMusicVolume))savedMusicVolume=20;
music.volume=Math.max(0,Math.min(100,savedMusicVolume))/100;$('music-volume').value=music.volume*100;
function musicTrackLabel(){
 const credited=musicServer!=='sinsworld';
 $('music-description').textContent=(musicServer==='solo'?'Solo Leveling':musicServer==='naruto'?'Наруто':'SinsWorld')+' · '+musicTracks[musicIndex][1];
 $('music-credits').hidden=!credited;
 $('music-cc-credits').hidden=musicServer==='solo';$('music-solo-credits').hidden=musicServer!=='solo';$('music-credits-tracks').textContent=musicTracks.map(t=>t[1]).join(', ');
}
function musicSetServer(id){
 if(musicServer===id){musicTrackLabel();return;}
 music.pause();musicServer=id;musicTracks=serverMusic[id]||serverMusic.sinsworld;musicIndex=0;
 music.src=musicTracks[0][0];music.load();musicTrackLabel();musicTryPlay();
}
function musicTryPlay(){if(musicEnabled&&!musicGameRunning&&!document.hidden&&!$('app').hidden)music.play().catch(()=>{});}
function musicControls(){const label=musicEnabled?'Выключить музыку':'Включить музыку';$('music-toggle').setAttribute('aria-label',label);$('music-toggle').title=label;$('music-toggle').setAttribute('aria-pressed',String(musicEnabled));$('music-toggle').classList.toggle('muted',!musicEnabled);$('music-setting-toggle').textContent=musicEnabled?'Выключить':'Включить';}
function toggleMusic(){musicEnabled=!musicEnabled;localStorage.setItem('sinsworld.music.enabled',String(musicEnabled));if(musicEnabled)musicTryPlay();else music.pause();musicControls();}
$('music-toggle').onclick=toggleMusic;$('music-setting-toggle').onclick=toggleMusic;
$('music-volume').oninput=e=>{music.volume=Number(e.target.value)/100;localStorage.setItem('sinsworld.music.volume',e.target.value);};
music.onended=()=>{musicIndex=(musicIndex+1)%musicTracks.length;music.src=musicTracks[musicIndex][0];musicTrackLabel();musicTryPlay();};
music.onerror=()=>{music.pause();$('music-toggle').title='Музыка недоступна';};
document.addEventListener('pointerdown',musicTryPlay);document.addEventListener('keydown',musicTryPlay);
document.addEventListener('visibilitychange',()=>{if(document.hidden)music.pause();else musicTryPlay();});musicControls();

// Polling is a fallback when a native event was missed during page/bridge setup.
let launchSequence=-1,launchPollPending=false;
setInterval(async()=>{
 if(!nativeReady||launchPollPending)return;
 launchPollPending=true;
 try{
  const state=await api('get_launch_state');
  if(state.sequence!==launchSequence){launchSequence=state.sequence;window.receiveLauncherEvent(state);}
  setBusy(!!state.busy);
 }catch{}finally{launchPollPending=false;}
},750);

// Upcoming servers preview their themes; only SinsWorld has a launch route.
$('server-sinsworld').onclick=()=>chooseServer('sinsworld');
$('server-naruto').onclick=()=>chooseServer('naruto');
$('server-solo').onclick=()=>chooseServer('solo');
$('server-selector').querySelector('summary').addEventListener('click',event=>{if(busy)event.preventDefault();});
document.addEventListener('click',event=>{if(!$('server-selector').contains(event.target))$('server-selector').open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('server-selector').open){$('server-selector').open=false;$('server-selector').querySelector('summary').focus();}});

function applyServer(id){
 selectedServer=['naruto','solo'].includes(id)?id:'sinsworld';
 const upcoming=selectedServer!=='sinsworld',solo=selectedServer==='solo';
 document.documentElement.dataset.server=selectedServer;
 $('app-stage').dataset.server=selectedServer;
 $('selected-server-name').textContent=upcoming?(solo?'Solo Leveling':'Наруто'):'SinsWorld';
 $('server-wordmark').innerHTML=upcoming?(solo?'SOLO<br>LEVELING':'NARUTO'):'SINS <span>WORLD</span>';
 $('server-subtitle').hidden=!upcoming;
 $('server-subtitle').textContent=solo?'ВОССТАНЬ ИЗ ТЕНИ':'МИР ШИНОБИ';
 $('play-label').textContent=upcoming?'Скоро открытие':'Играть';
 $('play-icon').textContent=upcoming?'⌑':'▶';
 for(const id of ['sinsworld','naruto','solo']){const b=$('server-'+id);b.classList.toggle('selected',id===selectedServer);b.setAttribute('aria-pressed',String(id===selectedServer));}
 setBusy(busy);
 if(!busy)$('operation-text').textContent=upcoming?'Сервер готовится к открытию':'Готов к запуску';
 setupVideo(settings.trailer_url);
 musicSetServer(selectedServer);
 try{splash().setTheme(selectedServer);}catch{}
}
async function chooseServer(id){
 if(busy)return;
 try{if(nativeReady){const r=await api('select_server',id);if(!r.ok)throw Error(r.error);}applyServer(id);$('server-selector').open=false;$('server-selector').querySelector('summary').focus();}catch(e){toast(e.message);}
}
$('splash-frame').addEventListener('load',()=>{try{splash().setTheme(selectedServer);}catch{}});
$('download-new').onclick=async()=>{$('download-new').disabled=true;$('required-update-status').textContent='Начинаем загрузку…';try{const r=await api('install_launcher_update');if(!r.ok)throw Error(r.error);}catch(e){$('download-new').disabled=false;$('required-update-status').textContent=e.message;}};
$('update-close').onclick=window.closeLauncher;

$('skin-mode').onchange=()=>{const mode=$('skin-mode').value;$('skin-file-row').hidden=mode!=='png';$('skin-value-row').hidden=mode==='png';$('skin-value-label').textContent=mode==='url'?'HTTPS-ссылка на PNG':'Ник Minecraft';$('skin-value').placeholder=mode==='url'?'https://…/skin.png':'Введите ник';$('skin-upload-hint').hidden=mode==='name';$('skin-result').textContent='';};
$('skin-pick').onclick=async()=>{try{const r=await api('choose_skin_png');if(!r.ok)throw Error(r.error);if(r.cancelled)return;$('skin-file-name').textContent=r.name;$('skin-preview').src=r.skin_data;$('skin-preview').hidden=false;}catch(e){$('skin-result').textContent=e.message;}};
$('skin-save').onclick=async()=>{if(busy)return;$('skin-save').disabled=true;try{const r=await api('change_skin',$('skin-mode').value,$('skin-value').value.trim(),$('skin-variant').value);if(!r.ok)throw Error(r.error);}catch(e){$('skin-result').textContent=e.message;$('skin-save').disabled=false;}};

function updateNavIndicator(){
 const nav=document.querySelector('nav'),active=nav.querySelector('button.active'),indicator=nav.querySelector('.nav-indicator');
 if(!active||!active.offsetWidth)return;
 indicator.style.width=active.offsetWidth+'px';
 indicator.style.transform='translateX('+active.offsetLeft+'px)';
 // Place the first selection before enabling transitions.
 if(!nav.classList.contains('motion-ready'))requestAnimationFrame(()=>nav.classList.add('motion-ready'));
 nav.querySelectorAll('[data-page]').forEach(button=>{
  if(button===active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
 });
}
const navResizeObserver=new ResizeObserver(updateNavIndicator);
navResizeObserver.observe(document.querySelector('nav'));
document.fonts.ready.then(updateNavIndicator);

