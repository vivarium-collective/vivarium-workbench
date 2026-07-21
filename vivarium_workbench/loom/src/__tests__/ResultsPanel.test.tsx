// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ResultsPanel } from '../panels/ResultsPanel';
import { runDownloadUrl } from '../api';

afterEach(() => { cleanup(); });

describe('runDownloadUrl', () => {
  it('returns the correct download endpoint path', () => {
    expect(runDownloadUrl('r1')).toBe('/api/composite-run/r1/download');
    expect(runDownloadUrl('abc-123')).toBe('/api/composite-run/abc-123/download');
  });
});

describe('ResultsPanel download link', () => {
  it('renders the download link when downloadable=true and runId is set', () => {
    render(
      <ResultsPanel
        trajectory={null}
        hasRun={true}
        runId="run-42"
        downloadable={true}
      />
    );
    const link = screen.getByRole('link', { name: /download results/i });
    expect(link).toBeTruthy();
    expect((link as HTMLAnchorElement).href).toContain('/api/composite-run/run-42/download');
    expect(link.hasAttribute('download')).toBe(true);
  });

  it('does NOT render the download link when downloadable=false', () => {
    render(
      <ResultsPanel
        trajectory={null}
        hasRun={true}
        runId="run-42"
        downloadable={false}
      />
    );
    expect(screen.queryByRole('link', { name: /download results/i })).toBeNull();
  });

  it('does NOT render the download link when runId is null', () => {
    render(
      <ResultsPanel
        trajectory={null}
        hasRun={true}
        runId={null}
        downloadable={true}
      />
    );
    expect(screen.queryByRole('link', { name: /download results/i })).toBeNull();
  });

  it('does NOT render the download link when neither prop is provided', () => {
    render(
      <ResultsPanel
        trajectory={null}
        hasRun={false}
      />
    );
    expect(screen.queryByRole('link', { name: /download results/i })).toBeNull();
  });

  it('readOnly + no trajectory shows the live-only message', () => {
    render(<ResultsPanel trajectory={null} hasRun={false} readOnly />);
    expect(screen.getByText(/read-only mirror|live dashboard/i)).toBeTruthy();
  });

  it('renders the download link alongside trajectory data', () => {
    const trajectory = [
      { step: 1, state: { obs: { val: 1 } } },
      { step: 2, state: { obs: { val: 2 } } },
    ];
    render(
      <ResultsPanel
        trajectory={trajectory}
        hasRun={true}
        runId="run-99"
        downloadable={true}
      />
    );
    const link = screen.getByRole('link', { name: /download results/i });
    expect(link).toBeTruthy();
    expect((link as HTMLAnchorElement).href).toContain('/api/composite-run/run-99/download');
    // The observable table is also rendered.
    expect(screen.getByText('obs')).toBeTruthy();
  });
});
