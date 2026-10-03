import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { AppShell, RequireAdmin, RequireAuth } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import Admin from "./pages/Admin";
import Library from "./pages/Library";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import OidcCallback from "./pages/OidcCallback";
import Recordings from "./pages/Recordings";
import Runs from "./pages/Runs";
import SkillDetail from "./pages/SkillDetail";
import SkillEditor from "./pages/SkillEditor";

function Root() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Outlet />
      </ToastProvider>
    </AuthProvider>
  );
}

const router = createBrowserRouter([
  {
    element: <Root />,
    children: [
      { path: "/login", element: <Login /> },
      { path: "/auth/callback", element: <OidcCallback /> },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              { path: "/", element: <Library /> },
              { path: "/skills/new", element: <SkillEditor /> },
              { path: "/skills/:id", element: <SkillDetail /> },
              { path: "/skills/:id/edit", element: <SkillEditor /> },
              { path: "/skills/:id/runs", element: <Runs /> },
              { path: "/recordings", element: <Recordings /> },
              { element: <RequireAdmin />, children: [{ path: "/admin", element: <Admin /> }] },
              { path: "*", element: <NotFound /> },
            ],
          },
        ],
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} future={{ v7_startTransition: true }} />;
}
