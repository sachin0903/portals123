"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, MessageSquare, Plug, GitBranch, Bot,
  Server, Users, ScrollText, Cpu, ChevronDown, Settings,
  Bell, LogOut, Shield,
} from "lucide-react";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup,
  SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuTrigger, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/chat", label: "Chat", icon: MessageSquare, badge: "3" },
];

const adminItems = [
  { href: "/admin/providers", label: "Chat Providers", icon: Plug },
  { href: "/admin/channels", label: "Channel Binding", icon: GitBranch },
  { href: "/admin/agents", label: "Agent Config", icon: Bot },
  { href: "/admin/mcp-servers", label: "MCP Servers", icon: Server },
  { href: "/admin/users", label: "Users & Teams", icon: Users },
  { href: "/admin/audit", label: "Audit Logs", icon: ScrollText },
  { href: "/admin/llm", label: "LLM Providers", icon: Cpu },
];

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <Sidebar variant="inset" collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              render={
                <Link href="/dashboard">
                  <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                    <Shield className="size-4" />
                  </div>
                  <div className="grid flex-1 text-left text-sm leading-tight">
                    <span className="truncate font-semibold">Graphene</span>
                    <span className="truncate text-xs text-muted-foreground">ChatOps AI Platform</span>
                  </div>
                </Link>
              }
            />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Platform</SidebarGroupLabel>
          <SidebarMenu>
            {navItems.map((item) => (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  isActive={pathname === item.href}
                  tooltip={item.label}
                  render={
                    <Link href={item.href}>
                      <item.icon />
                      <span>{item.label}</span>
                      {item.badge && (
                        <Badge variant="destructive" className="ml-auto text-xs px-1.5 py-0">
                          {item.badge}
                        </Badge>
                      )}
                    </Link>
                  }
                />
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel>Administration</SidebarGroupLabel>
          <SidebarMenu>
            {adminItems.map((item) => (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  isActive={pathname === item.href}
                  tooltip={item.label}
                  render={
                    <Link href={item.href}>
                      <item.icon />
                      <span>{item.label}</span>
                    </Link>
                  }
                />
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
                    <Avatar className="h-8 w-8 rounded-lg">
                      <AvatarFallback className="rounded-lg bg-primary text-primary-foreground">SC</AvatarFallback>
                    </Avatar>
                    <div className="grid flex-1 text-left text-sm leading-tight">
                      <span className="truncate font-semibold">Sarah Chen</span>
                      <span className="truncate text-xs text-muted-foreground">admin</span>
                    </div>
                    <ChevronDown className="ml-auto size-4" />
                  </SidebarMenuButton>
                }
              />
              <DropdownMenuContent side="top" className="w-56" align="start">
                <DropdownMenuItem>
                  <Settings className="mr-2 h-4 w-4" /> Settings
                </DropdownMenuItem>
                <DropdownMenuItem>
                  <Bell className="mr-2 h-4 w-4" /> Notifications
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-destructive">
                  <LogOut className="mr-2 h-4 w-4" /> Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
