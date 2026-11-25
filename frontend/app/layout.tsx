import type { Metadata } from "next";
import { Montserrat } from "next/font/google";
import StartupSplash from "@/components/layout/StartupSplash";
import GlassPointerEffects from "@/components/ui/GlassPointerEffects";
import SpatialBackground from "@/components/ui/SpatialBackground";
import "./globals.css";
import "./glass.css";
import "./spatial.css";
import "./profile.css";
import "./workspace-tools.css";

// Montserrat auto-hospedada pelo next/font: baixada no build, sem requisições ao Google em tempo de execução.
const montserrat = Montserrat({
  subsets: ["latin", "latin-ext"],
  display: "swap",
  variable: "--font-montserrat",
});

export const metadata: Metadata = {
  title: "QuantumOne",
  description:
    "Ferramenta inteligente para análise e otimização de código quântico.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="pt-BR"
      className={`${montserrat.variable} h-full antialiased`}
    >
      <head>
        <link rel="preload" href="/intro/quantumone-q1.glb" as="fetch" crossOrigin="anonymous" />
      </head>
      <body className="min-h-full flex flex-col">
        <SpatialBackground />
        <GlassPointerEffects />
        <StartupSplash>{children}</StartupSplash>
      </body>
    </html>
  );
}
