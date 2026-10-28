// Sidebar: Chat, Jobs, Market, Outreach, Research, Approvals, Profile, Settings
import Link from "next/link";

const NAV = ["chat", "jobs", "market", "outreach", "research", "approvals", "profile", "settings"];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <nav className="w-52 border-r p-4 flex flex-col gap-2">
        {NAV.map((n) => (
          <Link key={n} href={`/${n}`} className="capitalize hover:underline">{n}</Link>
        ))}
      </nav>
      <main className="flex-1">{children}</main>
    </div>
  );
}
