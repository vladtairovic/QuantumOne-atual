import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title:"GrowUp | RAG e Qiskit",description:"Busca documental e otimização de circuitos"};
export default function RootLayout({children}: {children: React.ReactNode}) {
 return <html lang="pt-BR"><body>{children}</body></html>;
}
