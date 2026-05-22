import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "finRAG - 金融文档智能问答",
  description: "研报/财报深度分析助手",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="h-screen overflow-hidden bg-[#fafafa] text-neutral-900 antialiased">
        {children}
      </body>
    </html>
  );
}
