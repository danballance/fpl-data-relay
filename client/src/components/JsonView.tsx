export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="json-view" data-testid="json-view">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
