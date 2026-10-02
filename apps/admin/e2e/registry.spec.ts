import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

const operator = "e2e-operator-" + "a".repeat(32);
const viewer = "e2e-viewer-" + "v".repeat(32);
test.skip(
  !process.env.AGENTICIOT_TEST_DATABASE_URL,
  "Requires a disposable PostgreSQL test database",
);

test("publish, register, discover and edit a device through the real API", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const suffix = randomUUID().replaceAll("-", "");
  const modelKey = `light_${suffix}`;
  const modelTitle = `灯具 ${suffix.slice(0, 6)}`;
  const deviceTitle = `客厅灯 ${suffix.slice(0, 6)}`;
  await page.goto("/#models");
  await page.getByLabel("API 凭证", { exact: true }).fill(operator);
  await page.getByRole("button", { name: "连接工作区" }).click();
  const domainResponse = await page.request.get("/api/v1/management/domain", {
    headers: { Authorization: `Bearer ${operator}` },
  });
  const domain = await domainResponse.json();
  expect(domain.id).not.toBe(process.env.AGENTICIOT_E2E_DOMAIN);
  await expect(page.getByText(domain.id, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布型号", exact: true }).click();
  await page.getByLabel("型号标识", { exact: true }).fill(modelKey);
  await page.getByLabel("型号名称", { exact: true }).fill(modelTitle);
  await page.getByRole("button", { name: "使用虚拟灯具模板" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(
    page.getByText("型号版本已发布，能力定义已锁定。"),
  ).toBeVisible();
  await page.getByRole("link", { name: "设备注册", exact: true }).click();
  await page.getByRole("button", { name: "注册设备", exact: true }).click();
  await page.getByLabel("设备名称", { exact: true }).fill(deviceTitle);
  await page.getByLabel("设备标识", { exact: true }).fill(`virtual:${suffix}`);
  await page
    .getByRole("combobox", { name: "型号版本", exact: true })
    .selectOption({ label: `${modelTitle} / 1.0.0 (${modelKey})` });
  await page.getByLabel("空间引用（可选）").fill(`room:${suffix}`);
  await page.getByRole("button", { name: "确认注册" }).click();
  await expect(page.getByText("设备已注册，等待 Edge 接入。")).toBeVisible();
  const detail = page.getByRole("region", { name: "资源详情" });
  await expect(
    detail.getByRole("heading", { name: deviceTitle, exact: true }),
  ).toBeVisible();
  await detail
    .locator("summary")
    .filter({ hasText: /^set_power$/ })
    .click();
  await expect(
    detail
      .locator("details")
      .filter({
        has: page.locator("summary").filter({ hasText: /^set_power$/ }),
      })
      .locator("pre"),
  ).toBeVisible();

  const response = await page.request.get(
    `/api/v1/things?space_ref=room:${suffix}&capability=set_power`,
    { headers: { Authorization: `Bearer ${operator}` } },
  );
  expect(response.status()).toBe(200);
  const device = (await response.json()).items[0];
  expect(device.lifecycle_status).toBe("commissioning");
  expect(device.reachability).toBe("unknown");
  await page
    .getByLabel("编辑名称", { exact: true })
    .fill(`${deviceTitle} 已编辑`);
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByText("设备资料已更新。")).toBeVisible();
  await expect(
    detail.getByRole("heading", { name: `${deviceTitle} 已编辑`, exact: true }),
  ).toBeVisible();
  // Another authorized client edits first. The stale form must not overwrite it.
  const concurrent = await page.request.patch(
    `/api/v1/management/devices/${device.id}`,
    {
      headers: { Authorization: `Bearer ${operator}`, "If-Match": '"2"' },
      data: { title: `${deviceTitle} 远程更新` },
    },
  );
  expect(concurrent.status()).toBe(200);
  await page
    .getByLabel("编辑名称", { exact: true })
    .fill(`${deviceTitle} 过期修改`);
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByRole("alert")).toContainText("设备已被其他请求更新");
  await page.getByRole("button", { name: "重新加载详情" }).click();
  await expect(page.getByLabel("编辑名称", { exact: true })).toHaveValue(
    `${deviceTitle} 远程更新`,
  );
  await page
    .getByLabel("编辑名称", { exact: true })
    .fill(`${deviceTitle} 已编辑`);
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(
    detail.getByRole("heading", { name: `${deviceTitle} 已编辑`, exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
  ).toBe(false);
  await page.screenshot({
    path: testInfo.outputPath("registry.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "注册审计", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "变更记录", exact: true }),
  ).toBeVisible();
  const audits = await page.request.get("/api/v1/management/audit?limit=200", {
    headers: { Authorization: `Bearer ${operator}` },
  });
  expect(
    (await audits.json()).items.filter(
      (entry: { resource_id: string }) => entry.resource_id === device.id,
    ),
  ).toHaveLength(4); // registration + three successful edits, not the rejected stale edit
  expect(
    await page.evaluate(() => [localStorage.length, sessionStorage.length]),
  ).toEqual([0, 0]);
  await page.getByRole("button", { name: "断开", exact: true }).click();
  await expect(page.getByLabel("API 凭证", { exact: true })).toHaveValue("");
  await expect(page.locator("tbody tr")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("invalid credentials and read-only access are clearly represented", async ({
  page,
}) => {
  await page.goto("/#devices");
  await page.getByLabel("API 凭证", { exact: true }).fill("invalid");
  await page.getByRole("button", { name: "连接工作区" }).click();
  await expect(page.getByRole("alert")).toContainText("凭证无效");
  await page.getByLabel("API 凭证", { exact: true }).fill(viewer);
  await page.getByRole("button", { name: "连接工作区" }).click();
  await expect(page.getByText("viewer:e2e · 只读")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "注册设备", exact: true }),
  ).toHaveCount(0);
  const forbidden = await page.request.post("/api/v1/management/devices", {
    headers: { Authorization: `Bearer ${viewer}` },
    data: {},
  });
  expect(forbidden.status()).toBe(403);
  await page.reload();
  await expect(page.getByLabel("API 凭证", { exact: true })).toHaveValue("");
});
