import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import { expect, test } from "@playwright/test";
import { virtualLightCapabilities } from "../src/registry-api";

const operator = "e2e-operator-" + "a".repeat(32);
test.skip(
  !process.env.AGENTICIOT_TEST_DATABASE_URL,
  "Requires a disposable PostgreSQL database",
);

for (const mode of ["virtual", "mqtt"]) {
  test(`standalone ${mode} Edge confirms a UI command with observed evidence`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(60000);
    const children: ChildProcess[] = [];
    async function startDemo(args: string[]) {
      const child = spawn(
        ".venv/bin/python",
        ["-m", "agenticiot.mqtt_demo", ...args],
        {
          cwd: "../..",
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      children.push(child);
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(
          () => reject(new Error("MQTT demo startup timeout")),
          10000,
        );
        let output = "";
        child.stdout?.on("data", (data: Buffer) => {
          output += data.toString();
          if (output.includes("READY")) {
            clearTimeout(timeout);
            resolve();
          }
        });
        child.once("error", (error) => {
          clearTimeout(timeout);
          reject(error);
        });
        child.once("exit", (code) => {
          clearTimeout(timeout);
          reject(new Error(`MQTT demo exited ${code}`));
        });
      });
    }
    try {
      const suffix = crypto.randomUUID().replaceAll("-", "");
      const provisioned = await page.request.post("/api/v1/management/nodes", {
        headers: { Authorization: `Bearer ${operator}` },
        data: { edge_ref: `edge:e2e-${suffix}`, title: "Browser test Node" },
      });
      expect(provisioned.status()).toBe(201);
      const node = await provisioned.json();
      const token = node.token;
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const journal = testInfo.outputPath("edge-journal");
      let mqttPort = 0;
      if (mode === "mqtt") {
        const server = createServer();
        server.listen(0, "127.0.0.1");
        await once(server, "listening");
        const address = server.address();
        if (!address || typeof address === "string")
          throw new Error("Missing local port");
        mqttPort = address.port;
        await new Promise<void>((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        );
        await startDemo(["broker", "--port", String(mqttPort)]);
      }
      const startNode = () => {
        const child = spawn(
          "uv",
          [
            "run",
            "python",
            "-m",
            "agenticiot.node",
            "--url",
            "ws://127.0.0.1:8011/v1/nodes/channel",
            "--data-dir",
            journal,
            ...(mode === "mqtt"
              ? ["--mqtt-port", String(mqttPort), "--mqtt-namespace", suffix]
              : []),
          ],
          {
            cwd: "../..",
            env: { ...process.env, AGENTICIOT_NODE_TOKEN: token },
            stdio: ["ignore", "pipe", "pipe"],
          },
        );
        children.push(child);
      };
      const modelResponse = await page.request.post(
        "/api/v1/management/models",
        {
          headers: { Authorization: `Bearer ${operator}` },
          data: {
            ...virtualLightCapabilities,
            key: `execute_${suffix}`,
            title: `执行测试 ${suffix.slice(0, 6)}`,
            version: "1.0.0",
          },
        },
      );
      expect(modelResponse.status()).toBe(201);
      const model = await modelResponse.json();
      const deviceResponse = await page.request.post(
        "/api/v1/management/devices",
        {
          headers: { Authorization: `Bearer ${operator}` },
          data: {
            model_id: model.id,
            title: `虚拟灯 ${suffix.slice(0, 6)}`,
            external_ref: `edge-test:${suffix}`,
          },
        },
      );
      expect(deviceResponse.status()).toBe(201);
      const device = await deviceResponse.json();
      if (mode === "mqtt")
        await startDemo([
          "device",
          "--port",
          String(mqttPort),
          "--namespace",
          suffix,
          "--thing-id",
          device.id,
          "--data-dir",
          testInfo.outputPath("mqtt-device"),
        ]);
      const deviceProcess = children.at(-1);
      startNode();
      await expect
        .poll(
          async () => {
            const result = await page.request.get("/api/v1/management/nodes", {
              headers: { Authorization: `Bearer ${operator}` },
            });
            return (await result.json()).items.find(
              (n: { id: string }) => n.id === node.id,
            )?.online;
          },
          { timeout: 15000 },
        )
        .toBe(true);
      await page.goto("/#devices");
      await page.getByLabel("API 凭证", { exact: true }).fill(operator);
      await page.getByRole("button", { name: "连接工作区" }).click();
      await page
        .getByRole("button", { name: `查看 ${device.title}`, exact: true })
        .click();
      const runtime = page.getByRole("region", {
        name: "虚拟执行",
        exact: true,
      });
      await expect(runtime.getByText("暂无设备观测，状态未知。")).toBeVisible();
      await runtime
        .getByRole("combobox", { name: "已注册 Edge", exact: true })
        .selectOption(node.id);
      await runtime
        .getByRole("combobox", { name: "设备 Adapter", exact: true })
        .selectOption(
          mode === "mqtt" ? "mqtt-light-demo-v1" : "virtual-light-v1",
        );
      await runtime
        .getByRole("button", { name: "绑定虚拟 Edge", exact: true })
        .click();
      await expect(
        runtime.getByText(
          mode === "mqtt"
            ? /已绑定 mqtt-light-demo-v1/
            : /已绑定 virtual-light-v1/,
        ),
      ).toBeVisible();
      await expect
        .poll(
          async () => {
            const result = await page.request.get(
              `/api/v1/things/${device.id}/state`,
              {
                headers: { Authorization: `Bearer ${operator}` },
              },
            );
            return (await result.json()).properties.power?.value;
          },
          { timeout: 15000 },
        )
        .toBe(false);
      await runtime.getByRole("button", { name: "刷新状态与回执" }).click();
      await expect(
        runtime
          .locator(".observation-card")
          .filter({ hasText: "power" })
          .getByText("false", { exact: true }),
      ).toBeVisible();
      await runtime.getByRole("button", { name: "发送虚拟命令" }).click();
      await expect
        .poll(
          async () => {
            const result = await page.request.get(
              `/api/v1/things/${device.id}/commands`,
              {
                headers: { Authorization: `Bearer ${operator}` },
              },
            );
            return (await result.json())[0]?.status;
          },
          { timeout: 15000 },
        )
        .toBe("succeeded");
      await runtime.getByRole("button", { name: "刷新状态与回执" }).click();
      await expect(runtime.locator(".command-card summary")).toHaveText(
        "set_power · 观测确认成功",
      );
      await expect(
        runtime
          .locator(".observation-card")
          .filter({ hasText: "power" })
          .getByText("true", { exact: true }),
      ).toBeVisible();
      await expect(
        runtime.getByText("观测匹配", { exact: true }),
      ).toBeVisible();
      await expect(runtime.locator(".command-card ol li")).toHaveCount(4);
      await expect(
        page
          .getByRole("row")
          .filter({
            has: page.getByRole("button", {
              name: `查看 ${device.title}`,
              exact: true,
            }),
          })
          .getByText("连接：在线", { exact: true }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth > window.innerWidth,
        ),
      ).toBe(false);
      expect(errors).toEqual([]);
      await page.screenshot({
        path: testInfo.outputPath("execution.png"),
        fullPage: true,
      });
      await runtime.screenshot({
        path: testInfo.outputPath("execution-panel.png"),
      });
      if (mode === "mqtt") {
        const stopped = once(deviceProcess!, "exit");
        deviceProcess!.kill("SIGTERM");
        await stopped;
        await runtime
          .getByRole("combobox", { name: "开关目标", exact: true })
          .selectOption("false");
        await runtime.getByRole("button", { name: "发送虚拟命令" }).click();
        await expect
          .poll(
            async () => {
              const result = await page.request.get(
                `/api/v1/things/${device.id}/commands`,
                {
                  headers: { Authorization: `Bearer ${operator}` },
                },
              );
              return (await result.json())[0]?.status;
            },
            { timeout: 15000 },
          )
          .toBe("unknown");
        await runtime.getByRole("button", { name: "刷新状态与回执" }).click();
        await expect(
          runtime.locator(".command-card summary").first(),
        ).toHaveText("set_power · 结果未知，请勿自动重试");
        await expect(
          runtime.getByText(
            "设备可能已执行。系统未自动重发，请先核查设备状态。",
          ),
        ).toBeVisible();
        await expect(
          runtime
            .locator(".observation-card")
            .filter({ hasText: "power" })
            .getByText("true", { exact: true }),
        ).toBeVisible();
        await runtime.screenshot({
          path: testInfo.outputPath("mqtt-uncertain.png"),
        });
      }
    } finally {
      for (const child of children.reverse()) {
        if (child.exitCode === null && child.signalCode === null) {
          const exited = once(child, "exit");
          child.kill("SIGTERM");
          const force = setTimeout(() => child.kill("SIGKILL"), 5000);
          await exited;
          clearTimeout(force);
        }
        child.stdout?.destroy();
        child.stderr?.destroy();
      }
    }
  });
}
