"""Actual Lua harness fidelity, never a flight-path fixture or addon repair."""
from pathlib import Path
import subprocess


def test_overridden_frames_receive_actual_registered_events_without_duplicate_delivery(tmp_path):
    helper=Path(__file__).parents[1]/'workbench/real_checks/bug_frame_events.lua'
    script='''
local function values(...) return {n=select('#',...),...} end
'''+helper.read_text()+'''
local baseFrames={}
local function frame()
 local f={events={},scripts={},hooks={}}
 function f:RegisterEvent(e) self.events[e]=true end
 function f:UnregisterEvent(e) self.events[e]=nil end
 function f:UnregisterAllEvents() self.events={} end
 function f:SetScript(n,fn) self.scripts[n]=fn end
 function f:HookScript(n,fn) self.hooks[#self.hooks+1]=fn end
 return f
end
local function make()
 local f=frame();baseFrames[#baseFrames+1]=f;return f
end
local c={CreateFrame=make,Other='preserved'}
c.FireEvent=function(e,...)
 for _,f in ipairs(baseFrames) do
  if f.events[e] and f.scripts.OnEvent then f.scripts.OnEvent(f,e,...) end
 end
end
local identity=c
c=instrument_client_events(c)
assert(c==identity and c.Other=='preserved')
local originalCount,newCount,hooks,delegatedCount=0,0,0,0
local first=c.CreateFrame();first:RegisterEvent('PROBE')
first:SetScript('OnEvent',function() originalCount=originalCount+1 end)
local originalFactory=c.CreateFrame
-- Representative GUI-test replacement: registration is deliberately a noop;
-- the recorder must track the actual addon calls, not manufacture handlers.
c.CreateFrame=function()
 local f=frame()
 f.RegisterEvent=function() end
 f.UnregisterEvent=function() end
 f.UnregisterAllEvents=function() end
 return f
end
local second=c.CreateFrame();second:RegisterEvent('PROBE')
second:SetScript('OnEvent',function(self,e,flag,...)
 assert(self==second and e=='PROBE' and flag==false and select('#',...)==1 and select(1,...)==nil)
 newCount=newCount+1
end)
second:HookScript('OnEvent',function() hooks=hooks+1 end)
c.FireEvent('PROBE',false,nil)
assert(originalCount==1 and newCount==0 and hooks==0) -- committed setup preserved
activate_client_events(c)
c.FireEvent('OTHER',false,nil)
assert(newCount==0)
c.FireEvent('PROBE',false,nil)
assert(originalCount==2 and newCount==1 and hooks==1)
second:UnregisterEvent('PROBE');c.FireEvent('PROBE',false,nil)
assert(originalCount==3 and newCount==1)
second:RegisterEvent('PROBE');second:SetScript('OnEvent',nil)
c.FireEvent('PROBE',false,nil);assert(newCount==1 and hooks==2)
second:UnregisterAllEvents();c.FireEvent('PROBE',false,nil);assert(hooks==2)
c.CreateFrame=function(...) return originalFactory(...) end
local delegated=c.CreateFrame();delegated:RegisterEvent('PROBE')
delegated:SetScript('OnEvent',function() delegatedCount=delegatedCount+1 end)
c.FireEvent('PROBE',false,nil);assert(delegatedCount==1)
second:RegisterEvent('PROBE');second:SetScript('OnEvent',function() error('probe handler failed') end)
assert(not pcall(c.FireEvent,'PROBE',false,nil))
local plain={Other=true};assert(instrument_client_events(plain)==plain and plain.Other)
print('ACTUAL REGISTERED EVENT DELIVERY PASSED')
'''
    path=tmp_path/'frame-events.lua'
    path.write_text(script)
    run=subprocess.run(['lua5.1',str(path)],capture_output=True,text=True,timeout=5)
    assert run.returncode==0,run.stdout+run.stderr
    assert 'ACTUAL REGISTERED EVENT DELIVERY PASSED' in run.stdout
