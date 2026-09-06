const frame=document.getElementById('emulator');
const start=document.getElementById('start');
const status=document.getElementById('status');
const panel=document.getElementById('start-panel');
const error=document.getElementById('error');
const names={boing:'Boing Ball',juggler:'Juggler',newton:'Newton'};
let ready=false;

function selectDemo(id){
  document.querySelectorAll('[data-demo]').forEach(button=>{
    const active=button.dataset.demo===id;
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',String(active));
  });
  if(!ready)return;
  status.textContent=`Loading ${names[id]}…`;
  error.textContent='';
  frame.contentWindow.tsVideoDemo?.load(id);
}

window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==frame.contentWindow)return;
  if(event.data?.type==='tsvideo-ready'){
    ready=true;
    start.disabled=false;
    start.textContent='Start demo';
    status.textContent=`${names[event.data.demo]} cartridge loaded`;
  }else if(event.data?.type==='tsvideo-loaded'){
    status.textContent=`Running · ${names[event.data.demo]}`;
  }else if(event.data?.type==='tsvideo-loading'){
    status.textContent=`Loading ${names[event.data.demo]} tape…`;
  }else if(event.data?.type==='tsvideo-progress'){
    status.textContent=event.data.message;
  }else if(event.data?.type==='tsvideo-error'){
    error.textContent=event.data.message;
    status.textContent='Loading failed';
  }
});

start.addEventListener('click',()=>{
  if(!frame.contentWindow.tsVideoDemo?.start()){
    error.textContent='Audio is not ready. Try again in a moment, or download a DCK for another emulator.';
    return;
  }
  panel.hidden=true;
  const active=document.querySelector('[data-demo][aria-pressed=true]')?.dataset.demo||'boing';
  status.textContent=`Running · ${names[active]}`;
});

document.querySelectorAll('[data-demo]').forEach(button=>button.addEventListener('click',()=>selectDemo(button.dataset.demo)));
document.getElementById('reset').addEventListener('click',()=>frame.contentWindow.tsVideoDemo?.reset());
document.getElementById('fullscreen').addEventListener('click',()=>{
  const request=frame.requestFullscreen?.();
  request?.catch(()=>{status.textContent='Full screen unavailable in this browser';});
});
setTimeout(()=>{
  if(!ready)error.textContent='The live TSRun emulator is taking longer than expected. Reload, or download a DCK below.';
},20000);
