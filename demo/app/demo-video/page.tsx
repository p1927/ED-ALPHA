import type { Metadata } from "next"
import { DemoVideoPage } from "@/components/demo-video-page"

export const metadata: Metadata = {
  title: "ED-Alpha Walkthrough",
  description: "Compatibility URL for the timed ED-Alpha HTML walkthrough.",
}

export default function Page() {
  return <DemoVideoPage />
}
