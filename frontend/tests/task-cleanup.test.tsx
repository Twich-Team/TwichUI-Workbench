import {useState} from 'react';
import {render,screen,fireEvent} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import {CleanupReview,type CleanupChoice} from '../src/TaskCleanup';
import type {components} from '../src/api.generated';

const context:components['schemas']['CleanupContext']={intake_id:'saved-idea',title:'Inspect vendor labels',binding:'a'.repeat(64),revision:1,archived_at:'2026-11-11',member_count:1,can_archive:false,can_restore:true,can_delete:true,archive_reasons:[],delete_reasons:[],retention_explanation:'Only identity and cleanup receipts remain.',deletion_explanation:'Permanently removes the unused request and initial scope. Existing backups are retained.'};
const clients:QueryClient[]=[];
afterEach(()=>{clients.forEach(c=>c.clear());sessionStorage.clear();vi.unstubAllGlobals();});
function open(choice:CleanupChoice|null){
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(client);
 function Harness(){const [value,setValue]=useState(choice);return <CleanupReview choice={value} setChoice={setValue}/>;}
 return render(<QueryClientProvider client={client}><Harness/></QueryClientProvider>);
}
const ok=(value:unknown)=>({ok:true,json:async()=>value});
it('permanent deletion requires a fresh explicit confirmation and explains the exact removal',async()=>{
 const calls=vi.fn(async(url:string,init?:RequestInit)=>ok(url==='/api/session'?{csrf_token:'fresh'}:init?.method==='POST'?{command_id:'receipt',action:'delete',intake_ids:['saved-idea'],revisions:{'saved-idea':2},message:'Unused request permanently deleted.'}:context));vi.stubGlobal('fetch',calls);
 open({ids:['saved-idea'],action:'delete'});
 await screen.findByText(context.deletion_explanation);
 const button=screen.getByRole('button',{name:'Delete permanently'});expect(button).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox',{name:/Permanently remove/}));fireEvent.click(button);
 await screen.findByText('Unused request permanently deleted.');
 const posted=calls.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(posted[1]!.body as string)).toMatchObject({action:'delete',confirm_delete:true,targets:[{intake_id:'saved-idea',binding:context.binding}]});
});
it.each(['network','server'])('lost %s responses retain the same command across remount and recover its receipt',async(failure)=>{
 let lost=true;const commands:unknown[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url==='/api/session')return ok({csrf_token:'fresh'});
  if(init?.method==='POST'){commands.push(JSON.parse(init.body as string));if(lost){lost=false;if(failure==='network')throw Error('Controlled lost response');return {ok:false,status:502,json:async()=>({detail:'Controlled lost proxy response'})};}return ok({command_id:'same',action:'restore',intake_ids:['saved-idea'],revisions:{'saved-idea':2},message:'Task restored.'});}
  return ok(context);
 }));
 const first=open({ids:['saved-idea'],action:'restore'});const restore=await screen.findByRole('button',{name:'Restore task'});await vi.waitFor(()=>expect(restore).toBeEnabled());fireEvent.click(restore);
 await screen.findByRole('button',{name:'Recover saved action'});expect(sessionStorage.getItem('workbench-pending-cleanup')).not.toBeNull();first.unmount();
 open(null);fireEvent.click(await screen.findByRole('button',{name:'Recover saved action'}));await screen.findByText('Task restored.');
 expect(commands).toHaveLength(2);expect(commands[0]).toEqual(commands[1]);expect(sessionStorage.getItem('workbench-pending-cleanup')).toBeNull();
});
it('an unsafe deletion offers archive and keeps the reason in plain sight',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>ok({...context,archived_at:null,can_archive:true,can_restore:false,can_delete:false,delete_reasons:['Checks and PR history must be kept. Use Archive instead.']})));
 open({ids:['saved-idea'],action:'delete'});await screen.findByText('Checks and PR history must be kept. Use Archive instead.');
 expect(screen.getByRole('button',{name:'Delete permanently'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'Archive instead'}));
 await screen.findByRole('heading',{name:'Archive saved work?'});expect(screen.getByRole('button',{name:'Archive task'})).toBeEnabled();
});
