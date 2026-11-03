/** Routes. Everything but the login page needs a session. */
import { Suspense, lazy } from "react";
import type { ReactNode } from "react";
import { Navigate, createBrowserRouter, useLocation } from "react-router-dom";
import { useSession } from "./auth/session";
import { Layout } from "./components/Layout";
import { NotFoundPage, RouteErrorPage } from "./components/delight";
import { Loading } from "./components/ui";
import { AIPlaygroundPage } from "./pages/AIPlaygroundPage";
import { CompaniesPage, CompanyDetailPage } from "./pages/CompaniesPages";
import { ContactDetailPage, ContactsPage } from "./pages/ContactsPages";
import { DashboardPage } from "./pages/DashboardPage";
import { EnrollmentDetailPage, EnrollmentsPage } from "./pages/EnrollmentsPages";
import { EventsPage } from "./pages/EventsPage";
import { LeadsPage } from "./pages/LeadsPage";
import { AuthCallbackPage, LoginPage } from "./pages/LoginPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { OfficesPage } from "./pages/OfficesPage";
import { OutboxPage } from "./pages/OutboxPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { SuppressionsPage } from "./pages/SuppressionsPage";
import { TemplatesPage } from "./pages/TemplatesPage";
import { WorkflowListPage } from "./workflows/WorkflowListPage";

// The canvas (React Flow) is the heaviest screen: load it only when a workflow is opened.
const WorkflowEditorPage = lazy(() =>
  import("./workflows/WorkflowEditorPage").then((module) => ({ default: module.WorkflowEditorPage })),
);
// Screens used less often (scripts library, agents, countries, settings) load on first visit.
const ScriptsPage = lazy(() => import("./pages/ScriptsPage").then((module) => ({ default: module.ScriptsPage })));
const AgentsPage = lazy(() => import("./pages/AgentsPage").then((module) => ({ default: module.AgentsPage })));
const CountriesPage = lazy(() => import("./pages/CountriesPage").then((module) => ({ default: module.CountriesPage })));
const PipelinePage = lazy(() => import("./pages/PipelinePage").then((module) => ({ default: module.PipelinePage })));
const InboxPage = lazy(() => import("./pages/InboxPage").then((module) => ({ default: module.InboxPage })));
const IntegrationsPage = lazy(() => import("./pages/IntegrationsPage").then((module) => ({ default: module.IntegrationsPage })));
const FormsPage = lazy(() => import("./pages/FormsPage").then((module) => ({ default: module.FormsPage })));
const HelpPage = lazy(() => import("./pages/HelpPage").then((module) => ({ default: module.HelpPage })));
const QuotesPage = lazy(() => import("./pages/QuotesPage").then((module) => ({ default: module.QuotesPage })));
const InsightsPage = lazy(() => import("./pages/InsightsPage").then((module) => ({ default: module.InsightsPage })));
const PricingPage = lazy(() => import("./pages/PricingPage").then((module) => ({ default: module.PricingPage })));
const TasksPage = lazy(() => import("./pages/TasksPage").then((module) => ({ default: module.TasksPage })));
const LandingPage = lazy(() => import("./pages/LandingPage").then((module) => ({ default: module.LandingPage })));
const QuoteOnlinePage = lazy(() => import("./pages/QuoteOnlinePage").then((module) => ({ default: module.QuoteOnlinePage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));

function Deferred({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Loading />}>{children}</Suspense>;
}

function RequireSession({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (!session) {
    // Visitors who are not signed in see the public website at the root, not the sign-in page.
    if (location.pathname === "/") return <Suspense fallback={<div className="site-loading" />}><LandingPage /></Suspense>;
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage />, errorElement: <RouteErrorPage /> },
  { path: "/welcome", element: <Suspense fallback={<div className="site-loading" />}><LandingPage /></Suspense>, errorElement: <RouteErrorPage /> },
  // The customer's quote page: public, from the link in the quote email.
  { path: "/q/:token", element: <Suspense fallback={<Loading />}><QuoteOnlinePage /></Suspense>, errorElement: <RouteErrorPage /> },
  { path: "/auth/callback", element: <AuthCallbackPage />, errorElement: <RouteErrorPage /> },
  {
    path: "/",
    element: (
      <RequireSession>
        <Layout />
      </RequireSession>
    ),
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "workflows", element: <WorkflowListPage /> },
      {
        path: "workflows/:workflowId",
        element: (
          <Suspense fallback={<Loading label="Opening the editor…" />}>
            <WorkflowEditorPage />
          </Suspense>
        ),
      },
      { path: "enrollments", element: <EnrollmentsPage /> },
      { path: "enrollments/:enrollmentId", element: <EnrollmentDetailPage /> },
      { path: "events", element: <EventsPage /> },
      { path: "contacts", element: <ContactsPage /> },
      { path: "contacts/:contactId", element: <ContactDetailPage /> },
      { path: "companies", element: <CompaniesPage /> },
      { path: "companies/:companyId", element: <CompanyDetailPage /> },
      { path: "offices", element: <OfficesPage /> },
      { path: "templates", element: <TemplatesPage /> },
      { path: "scripts", element: <Deferred><ScriptsPage /></Deferred> },
      { path: "agents", element: <Deferred><AgentsPage /></Deferred> },
      { path: "countries", element: <Deferred><CountriesPage /></Deferred> },
      { path: "inbox", element: <Deferred><InboxPage /></Deferred> },
      { path: "reviews", element: <ReviewQueuePage /> },
      { path: "leads", element: <LeadsPage /> },
      { path: "pipeline", element: <Deferred><PipelinePage /></Deferred> },
      { path: "tasks", element: <Deferred><TasksPage /></Deferred> },
      { path: "quotes", element: <Deferred><QuotesPage /></Deferred> },
      { path: "pricing", element: <Deferred><PricingPage /></Deferred> },
      { path: "insights", element: <Deferred><InsightsPage /></Deferred> },
      { path: "help", element: <Deferred><HelpPage /></Deferred> },
      { path: "help/:slug", element: <Deferred><HelpPage /></Deferred> },
      { path: "outbox", element: <OutboxPage /> },
      { path: "notifications", element: <NotificationsPage /> },
      { path: "suppressions", element: <SuppressionsPage /> },
      { path: "ai", element: <AIPlaygroundPage /> },
      { path: "integrations", element: <Deferred><IntegrationsPage /></Deferred> },
      { path: "forms", element: <Deferred><FormsPage /></Deferred> },
      { path: "settings", element: <Deferred><SettingsPage /></Deferred> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
