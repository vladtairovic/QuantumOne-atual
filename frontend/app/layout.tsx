import type { Metadata } from "next";
import StartupSplash from "@/components/layout/StartupSplash";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agente RAG | Qiskit",
  description:
    "Ferramenta inteligente para análise e otimização de código quântico.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="pt-BR"
      className="h-full antialiased"
    >
      <head>
        <link rel="preload" href="/intro/quantumone-q1.glb" as="fetch" crossOrigin="anonymous" />
      </head>
      <body className="min-h-full flex flex-col">
        <StartupSplash>{children}</StartupSplash>
      </body>
    </html>
  );
}
