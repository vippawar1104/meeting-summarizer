import { FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, deleteLLM, fetchLLM, saveLLM, testLLM } from "../api";
import type { LLMSettings } from "../types";
import { Icon } from "./Icon";

export function ModelSettings({ installation, onChange }: { installation: number; onChange?: (usesOwnKey: boolean) => void }) {
  const [current, setCurrent] = useState<LLMSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "remove" | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const apply = useCallback((s: LLMSettings) => {
    setCurrent(s);
    onChange?.(s.configured);
    setProvider(s.provider ?? "");
    setModel(s.model ?? "");
    setBaseUrl(s.base_url ?? "");
    setApiKey(""); // the key is never kept in the page once it has been sent
  }, [onChange]);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      apply(await fetchLLM(installation));
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Could not load model settings.");
    }
  }, [installation, apply]);

  useEffect(() => {
    setCurrent(null);
    setMessage(null);
    void load();
  }, [load]);

  if (loadError) {
    return (
      <div className="error" role="status">
        <span>Could not load model settings: {loadError}</span>
        <button className="btn" onClick={() => void load()}>
          Try again
        </button>
      </div>
    );
  }
  if (!current) return <div className="empty">Loading…</div>;

  const info = current.providers.find((p) => p.id === provider);
  const label = current.providers.find((p) => p.id === current.provider)?.label ?? current.provider;
  const canSave = Boolean(provider && model.trim() && (apiKey.trim() || current.configured) && (!info?.needs_base_url || baseUrl.trim()));

  async function onSave(e: FormEvent) {
    e.preventDefault();
    setBusy("save");
    setMessage(null);
    try {
      const saved = await saveLLM(installation, {
        provider,
        model: model.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        ...(info?.needs_base_url ? { base_url: baseUrl.trim() } : {}),
      });
      apply(saved);
      setMessage({ kind: "ok", text: `Saved. Pull requests will now be reviewed with ${saved.model}.` });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof ApiError ? err.message : "Could not save. Please try again." });
    } finally {
      setBusy(null);
    }
  }

  async function onTest() {
    setBusy("test");
    setMessage(null);
    try {
      const result = await testLLM(installation);
      if (result.settings) apply(result.settings);
      setMessage(result.ok ? { kind: "ok", text: "The key works." } : { kind: "error", text: result.error ?? "The test failed." });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof ApiError ? err.message : "Could not run the test." });
    } finally {
      setBusy(null);
    }
  }

  async function onRemove() {
    setBusy("remove");
    setMessage(null);
    try {
      apply(await deleteLLM(installation));
      setMessage({ kind: "ok", text: "Removed. Pull requests will use Reviewly's built-in models." });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof ApiError ? err.message : "Could not remove the key." });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card model">
      <div className="model-status">
        {current.configured ? (
          <>
            <div className="name">
              <span className="pill">Your key</span>
              {label} · {current.model}
              <span className="muted"> · key {current.key_hint}</span>
            </div>
            <div className="status-line">
              {current.last_test_ok === false ? (
                <span className="status dead">
                  <Icon name="x-circle" size={14} />
                  Not working{current.last_test_error ? `: ${current.last_test_error}` : ""}
                </span>
              ) : (
                <span className="status done">
                  <Icon name="check-circle" size={14} />
                  Working
                </span>
              )}
            </div>
          </>
        ) : (
          <div className="name">
            <span className="pill">Built-in</span>
            Using Reviewly's built-in models
          </div>
        )}
      </div>

      <form className="model-form" onSubmit={onSave}>
        <label>
          <span>Provider</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} required>
            <option value="" disabled>
              Choose a provider
            </option>
            {current.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Model</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            list="model-suggestions"
            placeholder={info?.models[0] ?? "Model name, exactly as your provider spells it"}
            autoComplete="off"
            spellCheck={false}
            required
          />
          <datalist id="model-suggestions">
            {(info?.models ?? []).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>

        {info?.needs_base_url && (
          <label className="wide">
            <span>Base URL</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://openrouter.ai/api/v1"
              autoComplete="off"
              spellCheck={false}
              required
            />
          </label>
        )}

        <label className="wide">
          <span>
            API key
            {info?.key_url && (
              <a className="inline-link" href={info.key_url} target="_blank" rel="noreferrer">
                Get a key <Icon name="external-link" size={11} />
              </a>
            )}
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={current.configured ? `Saved (${current.key_hint}). Leave blank to keep it.` : "Paste your API key"}
            autoComplete="off"
            spellCheck={false}
          />
        </label>

        <div className="model-actions">
          <button className="btn primary" type="submit" disabled={!canSave || busy !== null}>
            <Icon name="key" size={14} />
            {busy === "save" ? "Testing and saving…" : "Save and test key"}
          </button>
          {current.configured && (
            <>
              <button className="btn" type="button" onClick={onTest} disabled={busy !== null}>
                <Icon name="refresh" size={14} />
                {busy === "test" ? "Testing…" : "Test again"}
              </button>
              <button className="btn" type="button" onClick={onRemove} disabled={busy !== null}>
                <Icon name="trash" size={14} />
                Remove
              </button>
            </>
          )}
        </div>
      </form>

      {message && (
        <div className={`note-line ${message.kind}`} role={message.kind === "error" ? "alert" : "status"}>
          <Icon name={message.kind === "ok" ? "check-circle" : "x-circle"} size={14} />
          {message.text}
        </div>
      )}

      <p className="fineprint">
        <Icon name="lock" size={12} /> Your key is encrypted, never shown again, and used only to review your repositories. With your own key,
        your code goes to the provider you choose instead of Reviewly's, and reviews don't count against the free plan.
      </p>
    </div>
  );
}
