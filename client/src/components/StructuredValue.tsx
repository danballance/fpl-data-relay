function scalar(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return String(value);
}

export function StructuredValue({ value }: { value: unknown }) {
  if (typeof value !== "object" || value === null) {
    return <span className={`value value--${typeof value}`}>{scalar(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="muted">Empty list</span>;
    }
    return (
      <ol className="structured-list">
        {value.map((item, index) => (
          <li key={index}>
            <StructuredValue value={item} />
          </li>
        ))}
      </ol>
    );
  }
  return (
    <dl className="structured-data">
      {Object.entries(value).map(([key, item]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>
            <StructuredValue value={item} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
