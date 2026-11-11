import {render,screen} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,expect,it,vi} from 'vitest';
import {SavedSetup} from '../src/SimpleModels';
let client:QueryClient;
afterEach(()=>{client?.clear();sessionStorage.clear();vi.unstubAllGlobals();});
it('saved setup distinguishes disabled fallback and ignores unsaved preferred-provider edits',async()=>{
 sessionStorage.setItem('model-policy-draft',JSON.stringify({preferred:'codex'}));
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({policy:{preferred:'local',substitution:'configured_fallback_only',test_fallback:'off',fallback:['codex'],fallback_on:['unavailable'],providers:{local:{enabled:true,model:'saved-local-model'},codex:{enabled:true,model:'saved-codex-model',effort:'high'}},pause_new_work:false}})})));
 client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><MemoryRouter><SavedSetup/></MemoryRouter></QueryClientProvider>);
 expect(await screen.findByText('Off · keep the approved AI')).toBeVisible();
 expect(screen.getByText('Local Qwen')).toBeVisible();expect(screen.getByText('saved-local-model')).toBeVisible();
 expect(screen.queryByText('saved-codex-model')).toBeNull();
});
