import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { ApiError } from "./api/client";
import { router } from "./App";
import { SessionProvider } from "./auth/session";
import { ToastProvider } from "./components/ui";
import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import { polyfillCountryFlagEmojis } from "country-flag-emoji-polyfill";
import flagFontUrl from "country-flag-emoji-polyfill/dist/TwemojiCountryFlags.woff2?url";
import { applyStoredTheme } from "./lib/theme";
import "./styles.css";

applyStoredTheme();
// Windows has no flag emoji glyphs: load a flags-only font (bundled, same origin) where needed.
polyfillCountryFlagEmojis("Twemoji Country Flags", flagFontUrl);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      // Client errors (404, 403, 422) will not fix themselves on retry.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
