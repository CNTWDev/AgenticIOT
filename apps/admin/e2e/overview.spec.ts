import { expect, test } from "@playwright/test";

test("overview uses the running backend and fits the viewport", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "运行概览", exact: true }),
  ).toBeVisible();
  const apiCard = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "平台 API" }) });
  await expect(apiCard.getByText("运行正常")).toBeVisible();
  await expect(page.getByText("待接入", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新状态" })).toBeEnabled();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  const spec = await page.request.get("/api/openapi.json");
  expect(spec.status()).toBe(200);
  expect(Object.keys((await spec.json()).paths)).toContain("/health/live");
  expect(errors).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("overview.png"),
    fullPage: true,
  });
});

test("disconnection is visible and a refresh recovers", async ({ page }) => {
  await page.route("**/api/health/**", (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.getByText("连接失败，请检查后台服务是否启动")).toHaveCount(
    2,
  );
  await page.unroute("**/api/health/**");
  await page.getByRole("button", { name: "刷新状态" }).click();
  const apiCard = page
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "平台 API" }) });
  await expect(apiCard.getByText("运行正常")).toBeVisible();
});
