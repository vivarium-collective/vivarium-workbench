// ConfigFileInput — file-upload control for a composite param declared
// `type: "config_file"`. Uploads the chosen file, has the server persist it
// under the workspace, and reports back the stored ABSOLUTE path as the param
// value — which the composite then loads wholesale (e.g. the vecoli composite's
// whole_config / fork_config → EcoliSim). The path is used as a param value
// only; it never feeds run_simulation's config_filename.
//
// Shared by both param forms (ConfigPanel's Configure tab and SetupRunPanel)
// so a config_file param renders identically wherever it appears.

import { useState, type ChangeEvent } from 'react';
import { persistCompositeConfig, fileToBase64 } from '../api';

export function ConfigFileInput(props: {
  id?: string;
  value: string;
  compositeId?: string | null;
  readOnly?: boolean;
  className?: string;
  onChange: (path: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onPick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const b64 = await fileToBase64(file);
      const res = await persistCompositeConfig(file.name, b64, props.compositeId || undefined);
      props.onChange(res.path);
    } catch (err) {
      // Fail loud, not silent: surface the reason so an upload that didn't take
      // is never mistaken for one that did.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="cfg-fileparam">
      <input
        id={props.id}
        type="file"
        className={props.className || 'sr-input'}
        disabled={props.readOnly || busy}
        onChange={onPick}
      />
      {busy && (
        <span className="cfg-file-status" style={{ marginLeft: 8, fontSize: 12, color: '#666' }}>
          uploading…
        </span>
      )}
      {error && (
        <div className="cfg-file-error" style={{ color: '#b00', fontSize: 12, marginTop: 4 }}>
          {error}
        </div>
      )}
      {props.value && !busy && (
        <div
          className="cfg-file-path"
          style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 12 }}
        >
          <code style={{ overflowWrap: 'anywhere' }} title={props.value}>{props.value}</code>
          <button
            type="button"
            className="cfg-file-clear"
            disabled={props.readOnly}
            title="Clear"
            onClick={() => props.onChange('')}
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
