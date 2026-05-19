import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigationType } from "react-router-dom";
import { AuthProvider } from "./lib/AuthContext";
import ProtectedRoute from "./components/layout/ProtectedRoute";
import Layout from "./components/layout/Layout";
import AircallPhonePanel from "./components/AircallPhone";
import Login from "./pages/Login";
import AuthCallback from "./pages/AuthCallback";
import Pipeline from "./pages/Pipeline";
import ImportPage from "./pages/Import";
import Companies from "./pages/Companies";
import CompanyDetail from "./pages/CompanyDetail";
import Contacts from "./pages/Contacts";
import ContactDetail from "./pages/ContactDetail";
import DealDetail from "./pages/DealDetail";
import Meetings from "./pages/Meetings";
import MeetingDetail from "./pages/MeetingDetail";
import SalesAnalytics from "./pages/SalesAnalytics";
import PreMeetingAssistance from "./pages/PreMeetingAssistance";
import AccountSourcing from "./pages/AccountSourcing";
import AccountSourcingCompanyDetail from "./pages/AccountSourcingCompanyDetail";
import AccountSourcingContactDetail from "./pages/AccountSourcingContactDetail";
import TeamManagement from "./pages/TeamManagement";
import SettingsPage from "./pages/Settings";
import TasksPage from "./pages/Tasks";
import { ToastProvider } from "./lib/ToastContext";

// Scroll to top on forward navigation; let browser handle back/forward scroll restoration
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
          <Routes>
            {/* Public routes */}
            <Route path="/login" element={<Login />} />
            <Route path="/auth/callback" element={<AuthCallback />} />

            {/* Protected routes */}
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
              {/* A few legacy nav links still point at older route names.
                  Keep them as aliases so bookmarks and sidebar labels continue to work. */}
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
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
