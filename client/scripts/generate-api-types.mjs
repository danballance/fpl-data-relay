import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";
import ts from "typescript";

const openApiUrl = process.env.RELAY_OPENAPI_URL;
if (openApiUrl === undefined || openApiUrl.trim() === "") {
  throw new Error(
    "RELAY_OPENAPI_URL is required, for example " +
      "http://127.0.0.1:8000/openapi.json.",
  );
}

const response = await fetch(openApiUrl);
if (!response.ok) {
  throw new Error(
    `Could not load relay OpenAPI schema: HTTP ${response.status}.`,
  );
}

const schema = await response.json();
const output = astToString(
  await openapiTS(schema, {
    transform: (_schemaObject, options) =>
      options.path === "#/components/schemas/JsonValue"
        ? ts.factory.createKeywordTypeNode(ts.SyntaxKind.UnknownKeyword)
        : undefined,
  }),
);
const outputUrl = new URL("../src/api/generated.ts", import.meta.url);
await writeFile(fileURLToPath(outputUrl), output, "utf8");
