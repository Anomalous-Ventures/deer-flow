import { redirect } from "next/navigation";

import { getServerSideUser } from "@/core/auth/server";

import { ControllerContent } from "./controller-content";

export const dynamic = "force-dynamic";

export default async function ControllerPage() {
  const result = await getServerSideUser();

  if (result.tag === "unauthenticated") {
    redirect("/login");
  }
  if (result.tag === "needs_setup" || result.tag === "system_setup_required") {
    redirect("/setup");
  }
  if (result.tag === "gateway_unavailable" || result.tag === "config_error") {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-2">
        <p className="text-muted-foreground">
          Service temporarily unavailable.
        </p>
      </div>
    );
  }
  if (result.user.system_role !== "admin") {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-2">
        <p className="text-muted-foreground">
          Admin role required to view the AI Dev Controller surface.
        </p>
      </div>
    );
  }

  return <ControllerContent />;
}
