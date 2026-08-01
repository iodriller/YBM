const STORAGE_KEY = "ybm-safety-tour-dismissed"

export function readSafetyTourDismissed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true"
  } catch {
    return false
  }
}

export function writeSafetyTourDismissed(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "true")
  } catch {
    // localStorage unavailable - banner just reappears next load, not fatal.
  }
}
