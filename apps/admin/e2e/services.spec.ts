import { expect, test } from "@playwright/test";

const operator = "e2e-operator-" + "a".repeat(32);
test.skip(
  !process.env.AGENTICIOT_TEST_DATABASE_URL,
  "Requires disposable PostgreSQL",
);

test("account-scoped Node and service activation stay distinct from connectivity", async ({
  page,
}, info) => {
  await page.goto("/#services");
  await page.getByLabel("管理凭证", { exact: true }).fill(operator);
  await page.getByRole("button", { name: "连接资源域" }).click();
  await expect(page.getByRole("heading", { name: "当前资源域" })).toBeVisible();
  const suffix = crypto.randomUUID().replaceAll("-", "");
  await page.getByLabel("节点名称", { exact: true }).fill(`节点 ${suffix}`);
  await page.getByLabel("节点标识", { exact: true }).fill(`node:${suffix}`);
  await page.getByRole("button", { name: "登记节点", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "节点凭证只展示一次" }),
  ).toBeVisible();
  await expect(page.getByLabel("节点凭证", { exact: true })).not.toHaveValue(
    "",
  );
  await page.getByRole("button", { name: "已保存，清除显示" }).click();
  const node = page.locator("article").filter({ hasText: `节点 ${suffix}` });
  await expect(node).toContainText("已启用 · 未连接");
  await page.getByLabel("所属节点").selectOption({ label: `节点 ${suffix}` });
  await page.getByLabel("服务名称", { exact: true }).fill(`model-${suffix}`);
  await page.getByLabel("Node 本地配置标识").fill("ollama");
  await page.getByLabel("获准模型").fill("tiny");
  await page.getByRole("button", { name: "注册为待批准服务" }).click();
  const service = page
    .locator("article")
    .filter({ hasText: `model-${suffix}` });
  await expect(service).toContainText("待批准");
  await service.getByRole("button", { name: "批准启用" }).click();
  await expect(service).toContainText("已批准启用");
  await expect(service).toContainText("声明为本地 · 健康未知 · Node 未连接");
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: info.outputPath("services.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "断开并清除凭证" }).click();
  await expect(page.getByLabel("管理凭证", { exact: true })).toHaveValue("");
});
