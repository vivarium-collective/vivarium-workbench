// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ResultsPanel } from '../panels/ResultsPanel';
import { runDownloadUrl } from '../api';

const HOME = window.location.pathname + window.location.search;
afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', HOME);
  delete (window as unknown as { __BASE_PATH__?: string }).__BASE_PATH__;
});

describe('runDownloadUrl', () => {
  it('returns a bare endpoint path when served at the root (no sub-path)', () => {
    expect(runDownloadUrl('r1')).toBe('/api/composite-run/r1/download');
    expect(runDownloadUrl('abc-123')).toBe('/api/composite-run/abc-123/download');
  });

  it('keeps the download inside a HeLx sub-path ingress', () => {
    // The loom iframe is served at <prefix>/bigraph-loom/index.html; the
    // download must be prefixed too, or it escapes to the ingress root.
    window.history.replaceState({}, '',
      '/user/phil/proxy/8080/bigraph-loom/index.html?id=demo');
    expect(runDownloadUrl('r1')).toBe(
      '/user/phil/proxy/8080/api/composite-run/r1/download');
  });

  it('honours an explicit ?apiBase= (published static bundle)', () => {
    window.history.replaceState({}, '', '/bigraph-loom/index.html?apiBase=/proj/site');
    expect(runDownloadUrl('r1')).toBe('/proj/site/api/composite-run/r1/download');
  });

  it('honours window.__BASE_PATH__ when a host injects it (matches Phil)', () => {
    (window as unknown as { __BASE_PATH__?: string }).__BASE_PATH__ = '/user/phil/proxy/8080';
    // even served at a bare path, the injected global wins
    expect(runDownloadUrl('r1')).toBe(
      '/user/phil/proxy/8080/api/composite-run/r1/download');
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
