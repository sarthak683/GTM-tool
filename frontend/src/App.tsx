import { lazy, Suspense, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigationType } from "react-router-dom";
import { AuthProvider } from "./lib/AuthContext";
import ProtectedRoute from "./components/layout/ProtectedRoute";
import Layout from "./components/layout/Layout";
import AircallPhonePanel from "./components/AircallPhone";
import { ToastProvider } from "./lib/ToastContext";

const Login = lazy(() => import("./pages/Login"));
const AuthCallback = lazy(() => import("./pages/AuthCallback"));
const Pipeline = lazy(() => import("./pages/Pipeline"));
const ImportPage = lazy(() => import("./pages/Import"));
const Companies = lazy(() => import("./pages/Companies"));
const CompanyDetail = lazy(() => import("./pages/CompanyDetail"));
const Contacts = lazy(() => import("./pages/Contacts"));
const ContactDetail = lazy(() => import("./pages/ContactDetail"));
const DealDetail = lazy(() => import("./pages/DealDetail"));
const Meetings = lazy(() => import("./pages/Meetings"));
const MeetingDetail = lazy(() => import("./pages/MeetingDetail"));
const SalesAnalytics = lazy(() => import("./pages/SalesAnalytics"));
const PreMeetingAssistance = lazy(() => import("./pages/PreMeetingAssistance"));
const AccountSourcing = lazy(() => import("./pages/AccountSourcing"));
const AccountSourcingCompanyDetail = lazy(() => import("./pages/AccountSourcingCompanyDetail"));
const AccountSourcingContactDetail = lazy(() => import("./pages/AccountSourcingContactDetail"));
const TeamManagement = lazy(() => import("./pages/TeamManagement"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const TasksPage = lazy(() => import("./pages/Tasks"));

function PageSkeleton() {
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 16, padding: "24px 20px",
      maxWidth: 900, margin: "0 auto", width: "100%",
    }}>
      <div style={{ height: 36, width: "40%", borderRadius: 10, background: "#e8eef5", animation: "pulse 1.5s ease-in-out infinite" }} />
      <div style={{ height: 16, width: "70%", borderRadius: 8, background: "#f0f4f9", animation: "pulse 1.5s ease-in-out infinite", animationDelay: "0.1s" }} />
      <div style={{ height: 16, width: "50%", borderRadius: 8, background: "#f0f4f9", animation: "pulse 1.5s ease-in-out infinite", animationDelay: "0.2s" }} />
      <div style={{ height: 200, borderRadius: 16, background: "#f4f7fb", border: "1px solid #e8eef5", animation: "pulse 1.5s ease-in-out infinite", animationDelay: "0.3s" }} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} style={{ height: 100, borderRadius: 14, background: "#f4f7fb", border: "1px solid #e8eef5", animation: "pulse 1.5s ease-in-out infinite", animationDelay: `${0.4 + i * 0.1}s` }} />
        ))}
      </div>
    </div>
  );
}

function ScrollToTop() {
  const { pathname } = useLocation();
  const navType = useNavigationType();
  useEffect(() => {
    if (navType === "PUSH") {
      window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
    }
  }, [pathname, navType]);
  return null;
}

const AIRCALL_STORAGE_KEY = "crm.aircall.enabled";

function AircallToggleListener() {
  const [enabled, setEnabled] = useState<boolean>(() => {
    return localStorage.getItem(AIRCALL_STORAGE_KEY) === "true";
  });

  useEffect(() => {
    function handleToggle() {
      setEnabled(localStorage.getItem(AIRCALL_STORAGE_KEY) === "true");
    }
    window.addEventListener("crm:aircall:toggle", handleToggle);
    return () => window.removeEventListener("crm:aircall:toggle", handleToggle);
  }, []);

  if (!enabled) return null;
  return <AircallPhonePanel />;
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <ToastProvider>
          <ScrollToTop />
          <AircallToggleListener />
          <Suspense fallback={<PageSkeleton />}>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/auth/callback" element={<AuthCallback />} />

              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <Layout />
                  </ProtectedRoute>
                }
              >
                <Route index element={<Navigate to="/pipeline" replace />} />
                <Route path="pipeline" element={<Pipeline />} />
                <Route path="import" element={<ImportPage />} />
                <Route path="companies" element={<Companies />} />
                <Route path="account-sourcing" element={<AccountSourcing />} />
                <Route path="account-sourcing/:id" element={<AccountSourcingCompanyDetail />} />
                <Route path="account-sourcing/contacts/:id" element={<AccountSourcingContactDetail />} />
                <Route path="companies/:id" element={<CompanyDetail />} />
                <Route path="contacts" element={<Contacts />} />
                <Route path="prospecting" element={<Contacts />} />
                <Route path="contacts/:id" element={<ContactDetail />} />
                <Route path="meetings" element={<PreMeetingAssistance />} />
                <Route path="meetings/manage" element={<Meetings />} />
                <Route path="pre-meeting-assistance" element={<PreMeetingAssistance />} />
                <Route path="meetings/:id" element={<MeetingDetail />} />
                <Route path="deals/:id" element={<DealDetail />} />
                <Route path="sales-analytics" element={<SalesAnalytics />} />
                <Route path="sales-analytics/:tab" element={<SalesAnalytics />} />
                <Route path="tasks" element={<TasksPage />} />
                <Route path="team" element={<TeamManagement />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
            </Routes>
          </Suspense>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
