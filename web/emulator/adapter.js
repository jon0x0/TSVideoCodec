// Thin project adapter around TSRun's live public ES modules.
// Upstream source: https://github.com/josef-jelinek/TSRun
const upstream='https://josef-jelinek.github.io/TSRun/';
const frameMs=1000*58688/3528000;
const demos={
  boing:{path:'../assets/BoingDemo.dck',type:'dck'},
  juggler:{path:'../assets/jugglerecm.dck',type:'dck'},
  newton:{path:'../assets/newton.tap',type:'tap'}
};
function notify(type,data={}){window.parent.postMessage({type,...data},location.origin);}
async function resource(path,binary=false){
  const response=await fetch(path);
  if(!response.ok)throw new Error(`Could not load ${path}: HTTP ${response.status}`);
  return binary?new Uint8Array(await response.arrayBuffer()):response.text();
}
async function boot(){
  const [cpu,video,sound,keys,pads]=await Promise.all(
    ['machine.js','screen.js','sound.js','keyboard.js','joystick.js'].map(path=>import(upstream+path)));
  const canvas=document.getElementById('screen');
  const matrix=new Uint8Array(8),joystick=new Uint8Array(2);
  const kbd=keys.initKeyboard(document.getElementById('keyboard'),matrix);
  pads.initJoysticks(joystick);
  let machine=cpu.createMachine(matrix,joystick),sfx=null;
  const [rom0,rom1,vert,frag]=await Promise.all([
    resource(upstream+'roms/ts2068-0.rom',true),resource(upstream+'roms/ts2068-1.rom',true),
    resource(upstream+'screen.vert.glsl'),resource(upstream+'screen.frag.glsl')]);
  if(rom0.length!==16384||rom1.length!==8192)throw new Error('Unexpected TS2068 system ROM sizes.');
  machine.homeRom.set(rom0);machine.exRom.set(rom1);
  let current='boing',currentBytes=null,tapeLoading=false;
  function runMachineFrames(count){
    for(let frame=0;frame<count;frame++){
      cpu.setVideoOn(machine,false);cpu.enableSound(machine,false);
      cpu.runFrame(machine);cpu.takeAudio(machine);
    }
  }
  function press(bits){
    for(const [row,bit] of bits)matrix[row]&=~(1<<bit);
    runMachineFrames(5);matrix.fill(31);runMachineFrames(8);
  }
  function autoloadTap(){
    notify('tsvideo-progress',{message:'Entering LOAD ""…'});
    runMachineFrames(100);
    press([[6,3]]);press([[7,1],[5,0]]);press([[7,1],[5,0]]);press([[6,0]]);
    notify('tsvideo-progress',{message:'Loading tape…'});
    let frames=0;
    while(machine.tape.playing&&!machine.tape.waiting&&frames<20000){runMachineFrames(1);frames++;}
    if(frames===20000)throw new Error('Tape did not finish loading.');
    cpu.setVideoOn(machine,true);cpu.enableSound(machine,true);cpu.runFrame(machine);cpu.takeAudio(machine);
    tapeLoading=false;
  }
  function insertCurrent(){
    machine=cpu.createMachine(matrix,joystick);
    machine.homeRom.set(rom0);machine.exRom.set(rom1);
    if(sfx){cpu.setSoundRate(machine,sfx.context.sampleRate);cpu.enableSound(machine,true);}
    const spec=demos[current];
    const insertTape=cpu.insertTape||cpu.insertTap;
    const mediaError=spec.type==='tap'?insertTape(machine,currentBytes):cpu.insertDock(machine,currentBytes);
    if(mediaError)throw new Error(mediaError);
    tapeLoading=spec.type==='tap';
    if(!tapeLoading){cpu.resetMachine(machine);return;}
    if(!sfx){cpu.resetMachine(machine);return;}
    if(cpu.autoloadTape){
      if(!cpu.autoloadTape(machine))throw new Error('TSRun could not prepare this tape for automatic loading.');
      tapeLoading=false;
    }else{
      cpu.resetMachine(machine);autoloadTap();
    }
  }
  async function load(id){
    if(!demos[id])throw new Error(`Unknown demo: ${id}`);
    keys.handleBlur(kbd);sound.resetSound(sfx);
    currentBytes=await resource(demos[id].path,true);
    current=id;
    if(demos[id].type==='tap')notify('tsvideo-loading',{demo:id});
    insertCurrent();notify('tsvideo-loaded',{demo:id});
  }
  currentBytes=await resource(demos[current].path,true);
  insertCurrent();
  const gfx=await new Promise((resolve,reject)=>video.initScreen(canvas,{vert,frag},(err,value)=>{
    if(err||!value)reject(new Error(err||'WebGL2 unavailable'));else resolve(value);
  }));
  video.setCrt(gfx,false);
  sfx=await new Promise((resolve,reject)=>sound.initSound(44100,(err,value)=>{
    if(err||!value)reject(new Error(err||'Web Audio unavailable'));else resolve(value);
  }));
  cpu.setSoundRate(machine,sfx.context.sampleRate);cpu.enableSound(machine,true);
  let last=0,carry=frameMs,started=false;
  function step(turbo=false){
    cpu.setVideoOn(machine,!turbo);cpu.enableSound(machine,!turbo);
    cpu.runFrame(machine);const chunk=cpu.takeAudio(machine);
    if(turbo)return;
    if(chunk.n>0&&(sound.soundIsRunning(sfx)||!sound.soundQueueReady(sfx)))sound.pushSound(sfx,chunk);
  }
  function frame(now){
    requestAnimationFrame(frame);pads.pollJoysticks(joystick);
    if(!last)last=now;carry+=Math.min(80,now-last);last=now;
    let ran=0;while(carry>=frameMs&&ran<4){step();carry-=frameMs;ran++;}
    const tapePlaying=machine.tape.state?machine.tape.state==='playing':machine.tape.playing&&!machine.tape.waiting;
    if(tapePlaying){
      while(ran<100&&(machine.tape.state?machine.tape.state==='playing':machine.tape.playing&&!machine.tape.waiting)){step(true);ran++;}
      step();
    }else if(started&&sound.soundIsRunning(sfx)&&!sound.soundQueueReady(sfx)&&ran<4){step();carry=Math.max(carry,0)-frameMs;}
    video.drawScreen(gfx,machine.pixels);
  }
  window.tsVideoDemo={
    start(){started=true;sound.resumeSound(sfx);window.focus();canvas.focus();return true;},
    load(id){this.start();load(id).catch(error=>notify('tsvideo-error',{message:error.message}));},
    reset(){keys.handleBlur(kbd);sound.resetSound(sfx);if(demos[current].type==='tap')notify('tsvideo-loading',{demo:current});insertCurrent();notify('tsvideo-loaded',{demo:current});}
  };
  window.addEventListener('keydown',event=>{window.tsVideoDemo.start();keys.handleKeyDown(kbd,event);});
  window.addEventListener('keyup',event=>keys.handleKeyUp(kbd,event));
  window.addEventListener('blur',()=>keys.handleBlur(kbd));
  window.addEventListener('pointerdown',()=>window.tsVideoDemo.start());
  window.addEventListener('resize',()=>video.resizeScreen(gfx));
  requestAnimationFrame(frame);notify('tsvideo-ready',{demo:current});
}
boot().catch(error=>{
  console.error(error);const message='TSRun could not start. '+error.message;
  document.getElementById('error').hidden=false;document.getElementById('error').textContent=message;
  notify('tsvideo-error',{message});
});
