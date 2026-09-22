import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Outfit } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// The story page's display face: geometric, heavy at the top of its range, tight when tracked in.
const outfit = Outfit({ variable: "--font-outfit", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "OneClick",
  description: "Smart guided troubleshooting for Galaxy devices.",
  applicationName: "OneClick",
  appleWebApp: { capable: true, title: "OneClick", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  themeColor: "#f1f0ea",
  viewportFit: "cover",
};

// Marks the document as scripted before first paint, so the story page can hide what its intro
// animates in without hiding it for anyone whose JavaScript never runs.
const MARK_JS = "document.documentElement.classList.add('js')";

// Typed by hand rather than with Next's generated `LayoutProps<"/">`: that global only exists
// once `.next/types` has been produced, so a fresh clone running the typecheck before a build
// would fail on it.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${outfit.variable} h-full`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: MARK_JS }} />
      </head>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
