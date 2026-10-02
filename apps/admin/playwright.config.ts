import { defineConfig, devices } from "@playwright/test";
import { randomUUID } from "node:crypto";

const testDatabase = process.env.AGENTICIOT_TEST_DATABASE_URL;
// Workers inherit one isolated domain per run; retries use separate Edge identities.
const testDomain =
  (process.env.AGENTICIOT_E2E_DOMAIN ??= `home:e2e-${randomUUID()}`);
const testClients = JSON.stringify([
  {
    token: "e2e-operator-" + "a".repeat(32),
    subject_ref: "operator:e2e",
    domain_ref: testDomain,
    role: "operator",
  },
  {
    token: "e2e-viewer-" + "v".repeat(32),
    subject_ref: "viewer:e2e",
    domain_ref: testDomain,
    role: "viewer",
  },
]);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:5181", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
  webServer: [
    {
      command:
        (testDatabase
          ? "uv run alembic upgrade head && uv run python -m agenticiot.access.service && "
          : "") +
        "uv run uvicorn agenticiot.main:app --host 127.0.0.1 --port 8011",
      env: {
        // Never inherit a developer's database or credentials for browser writes.
        AGENTICIOT_DATABASE_URL:
          testDatabase ?? "postgresql+psycopg://unused@127.0.0.1:1/unavailable",
        AGENTICIOT_API_CLIENTS: testDatabase ? testClients : "[]",
        AGENTICIOT_EDGE_CLIENTS: testDatabase
          ? JSON.stringify(
              ["desktop", "mobile"].flatMap((name) =>
                [0, 1].flatMap((attempt) =>
                  ["virtual", "mqtt"].map((mode) => ({
                    token:
                      `e2e-edge-${name}-${attempt}-${mode}-` + "e".repeat(32),
                    edge_ref: `edge:e2e-${name}-${attempt}-${mode}`,
                    domain_ref: testDomain,
                  })),
                ),
              ),
            )
          : "[]",
      },
      cwd: "../..",
      url: "http://127.0.0.1:8011/health/live",
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --port 5181",
      url: "http://127.0.0.1:5181",
      env: { API_PROXY_TARGET: "http://127.0.0.1:8011" },
      reuseExistingServer: false,
    },
  ],
});
