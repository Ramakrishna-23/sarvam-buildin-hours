import { NavLink, Route, Routes } from "react-router-dom";
import { DebugDashboard } from "./pages/DebugDashboard";
import { LiveDemo } from "./pages/LiveDemo";

export default function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>
          Basha <span>Bridge</span>
        </h1>
        <nav className="app-nav">
          <NavLink to="/" end>
            Live demo
          </NavLink>
          <NavLink to="/debug">Debug dashboard</NavLink>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<LiveDemo />} />
        <Route path="/debug" element={<DebugDashboard />} />
      </Routes>
    </div>
  );
}
