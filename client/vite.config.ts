import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

function requiredProxyTarget(
  mode: string,
  command: "build" | "serve",
): string | undefined {
  if (mode === "test" || command === "build") {
    return undefined;
  }
  const environment = loadEnv(mode, process.cwd(), "");
  const target =
    process.env.RELAY_API_PROXY_TARGET ??
    environment.RELAY_API_PROXY_TARGET;
  if (target === undefined || target.trim() === "") {
    throw new Error(
      "RELAY_API_PROXY_TARGET is required. Copy .env.example to .env.local.",
    );
  }
  return target;
}

export default defineConfig(({ command, mode }) => {
  const target = requiredProxyTarget(mode, command);
  const proxy =
    target === undefined
      ? undefined
      : {
          "/api": {
            target,
            changeOrigin: true,
            proxyTimeout: 0,
            rewrite: (path: string) => path.replace(/^\/api/, ""),
          },
        };

  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      clearMocks: true,
      restoreMocks: true,
      coverage: {
        provider: "v8",
        reporter: ["text", "html"],
        include: ["src/**/*.{ts,tsx}"],
        exclude: [
          "src/api/generated.ts",
          "src/main.tsx",
          "src/test/**",
          "src/**/*.d.ts"
        ],
        thresholds: {
          branches: 90,
          functions: 90,
          lines: 90,
          statements: 90
        }
      }
    }
  };
});
