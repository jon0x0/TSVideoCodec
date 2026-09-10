// Usage: node tests/verify_tsrun_demos.mjs /path/to/TSRun [dck ...]
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const upstream=path.resolve(process.argv[2]);
const cartridges=process.argv.slice(3);
if(!cartridges.length)cartridges.push('demos/BoingDemo.dck','demos/jugglerecm.dck','demos/newton.tap');
const api=await import(pathToFileURL(path.join(upstream,'machine.js')));

for(const cartridgePath of cartridges){
  const keys=new Uint8Array(8).fill(31),sticks=new Uint8Array(2).fill(255);
  const machine=api.createMachine(keys,sticks);
  machine.homeRom.set(fs.readFileSync(path.join(upstream,'roms/ts2068-0.rom')));
  machine.exRom.set(fs.readFileSync(path.join(upstream,'roms/ts2068-1.rom')));
  const media=fs.readFileSync(cartridgePath);
  const isTape=path.extname(cartridgePath).toLowerCase()==='.tap';
  if(!isTape)assert.equal(media.length,65545,`${cartridgePath}: unexpected DCK size`);
  const insertTape=api.insertTape||api.insertTap;
  const mediaError=isTape?insertTape(machine,media):api.insertDock(machine,media);
  assert.equal(mediaError,null,`${cartridgePath}: media insertion failed`);
  if(!isTape||!api.autoloadTape)api.resetMachine(machine);
  api.setSoundRate(machine,44100);api.enableSound(machine,true);
  function run(count,turbo=false){
    for(let frame=0;frame<count;frame++){
      api.setVideoOn(machine,!turbo);api.enableSound(machine,!turbo);api.runFrame(machine);api.takeAudio(machine);
    }
  }
  function press(bits){
    for(const [row,bit] of bits)keys[row]&=~(1<<bit);
    run(5);
    keys.fill(31);run(8);
  }
  if(isTape){
    if(api.autoloadTape){
      assert.equal(api.autoloadTape(machine),true,`${cartridgePath}: upstream autoload rejected the tape`);
    }else{
      run(100);
      press([[6,3]]);                // J: LOAD keyword
      press([[7,1],[5,0]]);         // Symbol Shift + P: quote
      press([[7,1],[5,0]]);         // closing quote
      press([[6,0]]);               // Enter
    }
    run(1);                         // Let the ROM enter/start the tape loader.
    let turboFrames=0;
    const tapePlaying=()=>machine.tape.state?machine.tape.state==='playing':machine.tape.playing&&!machine.tape.waiting;
    while(tapePlaying()&&turboFrames<20000){run(1,true);turboFrames++;}
    assert.ok(turboFrames<20000,`${cartridgePath}: tape did not finish loading`);
    run(1);
  }
  let changed=0,audioSamples=0;
  let previous=Buffer.from(machine.pixels);
  for(let frame=0;frame<360;frame++){
    api.runFrame(machine);
    const pixels=Buffer.from(machine.pixels);
    if(!pixels.equals(previous))changed++;
    previous=pixels;
    audioSamples+=api.takeAudio(machine).n;
  }
  assert.ok(changed>10,`${cartridgePath}: display did not animate`);
  assert.ok(audioSamples>0,`${cartridgePath}: emulator produced no audio samples`);
  const pixelValues=new Set(machine.pixels).size;
  assert.ok(pixelValues>2,`${cartridgePath}: final display is blank`);
  console.log(JSON.stringify({media:cartridgePath,type:isTape?'tap':'dck',frames:360,changedFrames:changed,audioSamples,pixelValues,pc:machine.cpu.pc,tapeState:machine.tape.state??(machine.tape.playing?'playing':machine.tape.waiting?'ready':'done')}));
}
