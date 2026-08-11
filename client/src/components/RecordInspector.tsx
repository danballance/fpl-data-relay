import { useState } from "react";
import type { ReactNode } from "react";

import { ApiDocsLink, type ApiOperation } from "./ApiDocsLink";
import { JsonView } from "./JsonView";
import { StatusPanel } from "./StatusPanel";
import { StructuredValue } from "./StructuredValue";

export function RecordInspector({
  title,
  record,
  loading,
  error,
  onClose,
  renderFields,
  apiOperation,
}: {
  title: string;
  record: unknown;
  loading: boolean;
  error: unknown;
  onClose: () => void;
  renderFields?: (record: unknown) => ReactNode;
  apiOperation?: ApiOperation;
}) {
  const [mode, setMode] = useState<"fields" | "json">("fields");

  return (
    <div className="inspector-backdrop" onMouseDown={onClose}>
      <section
        aria-label={`${title} details`}
        aria-modal="true"
        className="inspector"
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="inspector__header">
          <div>
            <p className="eyebrow">Record detail</p>
            <h2>{title}</h2>
          </div>
          <div className="inspector__actions">
            {apiOperation === undefined ? null : (
              <ApiDocsLink
                label="API endpoint"
                operation={apiOperation}
                variant="inline"
              />
            )}
            <button
              aria-label="Close details"
              className="icon-button"
              type="button"
              onClick={onClose}
            >
              ×
            </button>
          </div>
        </header>
        <div className="tab-list" role="tablist" aria-label="Detail format">
          <button
            aria-selected={mode === "fields"}
            className="tab"
            role="tab"
            type="button"
            onClick={() => setMode("fields")}
          >
            Fields
          </button>
          <button
            aria-selected={mode === "json"}
            className="tab"
            role="tab"
            type="button"
            onClick={() => setMode("json")}
          >
            Raw JSON
          </button>
        </div>
        <div className="inspector__body">
          {loading ? <StatusPanel state="loading" /> : null}
          {error === null ? null : <StatusPanel state="error" error={error} />}
          {!loading && error === null && mode === "fields" ? (
            renderFields === undefined ? (
              <StructuredValue value={record} />
            ) : (
              renderFields(record)
            )
          ) : null}
          {!loading && error === null && mode === "json" ? (
            <JsonView value={record} />
          ) : null}
        </div>
      </section>
    </div>
  );
}
