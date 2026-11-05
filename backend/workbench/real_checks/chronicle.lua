-- Trusted oracle. The model's tools cannot read/write this file.
dofile(TESTS .. 'harness.lua')
local function boot(saved)
 local c = MakeClient('Alpha', {'!!!TwichUI'})
 c.print=function() end
 c.TwichUIDB={}; c.TwichUIChronicleDB=saved; c.ZONE='Elwynn Forest'; c.LEVEL=12
 c.GetRealZoneText=function() return c.ZONE end; c.GetZoneText=c.GetRealZoneText
 c.UnitLevel=function() return c.LEVEL end; c.GetMoney=function() return 0 end
 c.UnitOnTaxi=function() return false end; c.IsSpellKnown=function() return false end
 c.NUM_CHAT_WINDOWS=1
 c.ChatFrame1={registered=true}
 c.ChatFrame1.IsEventRegistered=function(self) return self.registered end
 c.ChatFrame1.UnregisterEvent=function(self) self.registered=false end
 c.ChatFrame1.RegisterEvent=function(self) self.registered=true end
 c.requests=0; c.RequestTimePlayed=function() c.requests=c.requests+1 end
 c.LOADED['!!!TwichUI']=true
 c.FireEvent('ADDON_LOADED','!!!TwichUI'); c.FireEvent('PLAYER_ENTERING_WORLD',true,false)
 return c
end
local values={math.huge,-math.huge,0/0,-1,'bad'}
for _,bad in ipairs(values) do
 local c=boot(); local C,R=c.TwichUI.Chronicle,c.TwichUI.ChronicleRecorder
 assert(C.FormatDuration(bad)==nil,'invalid duration must be unavailable')
 local at,total=R.LevelTimes({baseLevel=12,baseTotal=100},13,bad,true)
 assert(at==nil and total==nil,'invalid measurement must not escape LevelTimes')
 c.FireEvent('TIME_PLAYED_MSG',1000,100)
 local before=C.Record().journey.baseTotal
 local note=C.Add('note',{note='Player note preserved'})
 c.LEVEL=13; c.FireEvent('PLAYER_LEVEL_UP',13)
 local entry
 for _,e in ipairs(C.Entries()) do if e.kind=='level' and e.level==13 then entry=e end end
 assert(entry,'pending level entry required')
 local callback='not delivered'
 R.RequestPlayed(function(value) callback=value==nil and 'unavailable' or value end)
 local ok=pcall(c.FireEvent,'TIME_PLAYED_MSG',bad,1)
 assert(ok,'invalid response must not throw')
 assert(not c.TwichUI.frame.events.TIME_PLAYED_MSG,'request handler must be cleaned up')
 assert(c.ChatFrame1.registered,'muted chat must be restored')
 assert(callback=='unavailable','callback must receive unavailable for an invalid total')
 assert(C.Find(note.id).note=='Player note preserved','player entry changed')
 assert(not entry.note and not entry.secsTotal,'invalid response must not fabricate details')
 assert(C.Record().journey.baseTotal==before,'invalid total must not poison baseline')
 c.LEVEL=14; c.FireEvent('PLAYER_LEVEL_UP',14)
 c.FireEvent('TIME_PLAYED_MSG',2000,100)
 assert(not c.TwichUI.frame.events.TIME_PLAYED_MSG,'subsequent valid request cleaned up')
 assert(C.Record().journey.baseTotal==2000,'later valid request must work')
 local saved=C.Add('level',{title='test measurement',secsAtLevel=bad,secsTotal=bad})
 assert(saved and not saved.secsAtLevel and not saved.secsTotal,'store must reject invalid measurements')
 C.SetDetails(saved.id,{secsAtLevel=bad,secsTotal=bad})
 assert(not saved.secsTotal,'detail update must reject invalid measurement')
 local loaded=boot({version=1,chars={['Alpha - Forever']={entries={
   {id=1,t=100,kind='note',title='Note',note='Saved note'}},nextId=2,
   journey={baseLevel=12,baseTotal=bad,lastLevel=12}}}})
 assert(loaded.TwichUI.Chronicle.Record().journey.baseTotal==nil,'invalid saved baseline')
 assert(loaded.TwichUI.Chronicle.Find(1).note=='Saved note','saved note must survive')
end
local c=boot(); local C,R=c.TwichUI.Chronicle,c.TwichUI.ChronicleRecorder
assert(C.FormatDuration(nil)==nil)
assert(C.FormatDuration(0)=='under a minute')
assert(C.FormatDuration(9240.5)=='2h 34m')
assert(C.FormatDuration(105240)=='1d 5h 14m')
local at,total=R.LevelTimes({baseLevel=12,baseTotal=100},13,200,true)
assert(at==100 and total==200,'valid arithmetic unchanged')
-- nil payload and malformed login baseline must also finish the request.
c.FireEvent('TIME_PLAYED_MSG',nil,nil)
assert(not c.TwichUI.frame.events.TIME_PLAYED_MSG,'nil response cleanup')
for _,bad in ipairs(values) do
 local b=boot();b.FireEvent('TIME_PLAYED_MSG',1000,bad)
 assert(b.TwichUI.Chronicle.Record().journey.baseTotal==nil,'invalid login baseline')
 assert(not b.TwichUI.frame.events.TIME_PLAYED_MSG,'login cleanup')
end
local multi=boot(); multi.FireEvent('TIME_PLAYED_MSG',1000,100)
multi.LEVEL=13;multi.FireEvent('PLAYER_LEVEL_UP',13)
multi.LEVEL=14;multi.FireEvent('PLAYER_LEVEL_UP',14)
multi.FireEvent('TIME_PLAYED_MSG',2000,10)
local found=0
for _,e in ipairs(multi.TwichUI.Chronicle.Entries()) do
 if e.kind=='level' and (e.level==13 or e.level==14) then
  assert(e.secsAtLevel==nil and e.secsTotal==2000,'multiple levels must not invent per-level duration')
  found=found+1
 end
end
assert(found==2 and not multi.TwichUI.frame.events.TIME_PLAYED_MSG,'multiple-level cleanup')
assert(multi.ChatFrame1.registered,'multiple-level chat restore')
print('ACCEPT OK chronicle independent invalid-measurement lifecycle')
