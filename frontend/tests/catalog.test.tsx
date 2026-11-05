import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import { SourceStatus, useSource } from '../src/Catalog';

function SourceProbe() { return <span>{useSource()}</span>; }
it('retains the maintenance source through a record detail route', () => {
  render(<MemoryRouter initialEntries={['/records/pilot/canonical-id']}><SourceProbe /></MemoryRouter>);
  expect(screen.getByText('pilot')).toBeVisible();
});
it('labels stale evidence without implying current inference', () => {
  render(<SourceStatus meta={{source:'pilot',local_records:false,version:'v1',partial:false,
    freshness:{state:'stale',observed_at:null,message:'Current execution cannot be confirmed.'}}} />);
  expect(screen.getByText('Maintenance · stale observations')).toBeVisible();
  expect(screen.getByText('Current execution cannot be confirmed.')).toBeVisible();
});
it('discloses partial results and unavailable evidence', () => {
  render(<SourceStatus meta={{source:'pilot',local_records:false,version:'v1',partial:true,
    freshness:{state:'unavailable',observed_at:null,message:'No state is inferred.'}}} />);
  expect(screen.getByText('Maintenance · unavailable')).toBeVisible();
  expect(screen.getByText(/Partial view/)).toBeVisible();
});
