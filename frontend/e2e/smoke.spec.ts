import { expect, type Page, test } from "@playwright/test"

const now = "2026-08-10T12:00:00Z"

function task(objective: string) {
  return {
    id: "task-smoke",
    objective,
    status: "received",
    conversation_id: "web",
    created_at: now,
    updated_at: now,
    metadata: {},
    artifacts: [],
  }
}

async function mockAdminApi(page: Page, onboardingComplete: boolean) {
  let tasks: ReturnType<typeof task>[] = []
  await page.route("**/admin/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/admin/, "")

    if (path === "/api/bootstrap") {
      return route.fulfill({
        json: {
          token_required: false,
          onboarding_complete: onboardingComplete,
          llm_reachable: onboardingComplete,
          version: "0.1.0-smoke",
        },
      })
    }
    if (path === "/api/chat/messages" && request.method() === "POST") {
      const body = request.postDataJSON() as { text: string }
      tasks = [task(body.text)]
      return route.fulfill({ json: { conversation_id: "web", task: tasks[0] } })
    }
    if (path === "/api/chat/messages") {
      return route.fulfill({ json: { conversation_id: "web", tasks } })
    }
    if (path === "/api/approvals") {
      return route.fulfill({ json: { approvals: [] } })
    }
    if (path === "/api/summary") {
      return route.fulfill({
        json: {
          status: "ok",
          tasks,
          task_pagination: { total: tasks.length },
          vscode: {
            connected: false,
            status: "waiting",
            last_seen_at: null,
            last_seen_age_seconds: null,
            heartbeat: null,
            state: null,
            pending_terminal_commands: 0,
          },
          warnings: [],
          config: {
            llm: { default_profile: "smoke" },
            adapters: { workspace: { enabled: true, root_dir: ".agent_control/workspaces" } },
          },
          database: { database_url: "sqlite:///smoke.db", path: "smoke.db" },
          integrations: {
            telegram: { enabled: false, token_present: false },
            llm: { default_profile_configured: onboardingComplete },
          },
        },
      })
    }
    return route.fulfill({ status: 404, json: { detail: `No smoke mock for ${path}` } })
  })
}

test("chat renders and a starter prompt sends the documented objective", async ({ page }) => {
  await mockAdminApi(page, true)
  await page.goto("./")

  await expect(page.getByRole("heading", { name: "What can I help you do?" })).toBeVisible()
  await expect(page.getByRole("textbox", { name: "Message YBM" })).toBeVisible()

  const sent = page.waitForRequest(
    (request) => request.url().endsWith("/admin/api/chat/messages") && request.method() === "POST",
  )
  await page.getByRole("button", { name: "Summarize the PDFs on my desktop" }).click()
  const request = await sent

  expect(request.postDataJSON()).toMatchObject({ text: "Summarize the PDFs on my desktop" })
})

test("an incomplete 390px setup uses one compact banner without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => localStorage.setItem("ybm.setup.dismissed", "1"))
  await mockAdminApi(page, false)
  await page.goto("./")

  await expect(page.getByText("No model configured yet.")).toBeVisible()
  await expect(page.getByText("Everything dangerous is off by default.")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Finish setup" })).toBeVisible()

  const dimensions = await page.evaluate(() => ({
    body: document.body.scrollWidth,
    viewport: window.innerWidth,
  }))
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport)
})
