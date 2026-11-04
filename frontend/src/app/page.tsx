"use client";

import {
  ArrowRight,
  ArrowUpRight,
  Bell,
  BarChart3,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  Clock3,
  Download,
  Ellipsis,
  FileText,
  GraduationCap,
  LayoutDashboard,
  Menu,
  Megaphone,
  MessageCircle,
  MoreHorizontal,
  Plus,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Users,
  UserRound,
  Wallet,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import PlatformAdminDashboard from "../components/PlatformAdminDashboard";
import FeedbackModal from "../components/FeedbackModal";
import BillingWorkspace from "../components/BillingWorkspace";
import AnalyticsWorkspace from "../components/AnalyticsWorkspace";
import AnnouncementWorkspace from "../components/AnnouncementWorkspace";
import { MessageModerationWorkspace } from "../components/MessagingWorkspace";
import TimetableWorkspace from "../components/TimetableWorkspace";
import TeacherDashboard from "../components/TeacherDashboard";
import { attendanceTenant, getCachedAccount } from "../lib/offline-attendance";
import { schoolApi } from "../lib/school-api";

type Section = "Overview" | "Analytics" | "Students" | "Attendance" | "Grades" | "Report cards" | "Fees" | "Announcements" | "Message moderation" | "Timetable";
type AttendanceState = "Present" | "Absent" | "Late";

type Student = {
  name: string;
  initials: string;
  grade: string;
  attendance: AttendanceState;
  color: string;
};

const initialStudents: Student[] = [
  { name: "Olivia Rhye", initials: "OR", grade: "Grade 8 · Cedar", attendance: "Present", color: "lilac" },
  { name: "Phoenix Baker", initials: "PB", grade: "Grade 7 · Maple", attendance: "Present", color: "peach" },
  { name: "Lana Steiner", initials: "LS", grade: "Grade 9 · Willow", attendance: "Late", color: "mint" },
  { name: "Demi Wilkinson", initials: "DW", grade: "Grade 8 · Cedar", attendance: "Present", color: "blue" },
  { name: "Candice Wu", initials: "CW", grade: "Grade 6 · Birch", attendance: "Absent", color: "rose" },
];

const navItems: { label: Section; icon: typeof LayoutDashboard }[] = [
  { label: "Overview", icon: LayoutDashboard },
  { label: "Students", icon: Users },
  { label: "Attendance", icon: ClipboardCheck },
  { label: "Grades", icon: BookOpen },
  { label: "Report cards", icon: FileText },
  { label: "Fees", icon: Wallet },
  { label: "Timetable", icon: CalendarDays },
];

function Logo() {
  return (
    <div className="brand">
      <div className="brand-mark"><GraduationCap size={21} strokeWidth={2.2} /></div>
      <div>
        <div className="brand-name">northstar</div>
        <div className="brand-caption">SCHOOL MANAGEMENT</div>
      </div>
    </div>
  );
}

function Avatar({ initials, color, small = false }: { initials: string; color: string; small?: boolean }) {
  return <span className={`avatar ${color}${small ? " avatar-small" : ""}`}>{initials}</span>;
}

function App() {
  const router = useRouter();
  const [isPlatformAdminHost, setIsPlatformAdminHost] = useState<boolean | null>(null);
  const [checkingTeacherSession, setCheckingTeacherSession] = useState(true);
  const [isTeacher, setIsTeacher] = useState(false);
  const [isSchoolAdmin, setIsSchoolAdmin] = useState(false);
  const [activeSection, setActiveSection] = useState<Section>("Overview");
  const [students, setStudents] = useState(initialStudents);
  const [query, setQuery] = useState("");
  const [showNotifications, setShowNotifications] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const todayLabel = new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(new Date());

  const filteredStudents = useMemo(
    () => students.filter((student) => `${student.name} ${student.grade}`.toLowerCase().includes(query.toLowerCase())),
    [students, query],
  );

  useEffect(() => {
    const platformAdminHost = window.location.hostname === "admin.localhost";
    setIsPlatformAdminHost(platformAdminHost);
    if (platformAdminHost) {
      setCheckingTeacherSession(false);
      return;
    }

    if (!window.sessionStorage.getItem("northstar_access_token")) {
      setCheckingTeacherSession(false);
      return;
    }

    let active = true;
    async function checkTeacherSession() {
      try {
        const account = await schoolApi<{ role: string }>("/api/v1/auth/me");
        if (active) {
          setIsTeacher(account.role === "teacher");
          setIsSchoolAdmin(account.role === "school_admin");
        }
      } catch (sessionError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        try {
          const cachedAccount = await getCachedAccount(attendanceTenant());
          if (cachedAccount?.role === "teacher") setIsTeacher(true);
          else setNotice(
            sessionError instanceof Error
              ? `${sessionError.message} The dashboard is unavailable until you reconnect.`
              : "The dashboard is unavailable until you reconnect.",
          );
        } catch (storageError: unknown) {
          setNotice(storageError instanceof Error ? storageError.message : "Unable to load your school session.");
        }
      } finally {
        if (active) setCheckingTeacherSession(false);
      }
    }
    void checkTeacherSession();
    return () => {
      active = false;
    };
  }, []);

  if (isPlatformAdminHost === null || checkingTeacherSession) {
    return <div className="workspace-loading">Loading Northstar...</div>;
  }
  if (isPlatformAdminHost) {
    return <PlatformAdminDashboard />;
  }
  if (isTeacher) return <TeacherDashboard />;

  const setAttendance = (name: string) => {
    setStudents((current) =>
      current.map((student) =>
        student.name === name
          ? { ...student, attendance: student.attendance === "Present" ? "Late" : "Present" }
          : student,
      ),
    );
    setNotice("Attendance updated for " + name);
  };

  const sectionTitle: Record<Section, string> = {
    Overview: "School overview",
    Analytics: "School analytics",
    Students: "Your students",
    Attendance: "Attendance",
    Grades: "Grades",
    "Report cards": "Student report cards",
    Fees: "Fees & payments",
    Announcements: "School announcements",
    "Message moderation": "Message moderation",
    Timetable: "Weekly timetable",
  };
  const sectionDescription: Record<Section, string> = {
    Overview: "A live view of enrollment, attendance, fees, and academic outcomes.",
    Analytics: "Explore current school performance using your tenant's recorded data.",
    Students: "Manage your school community, all in one place.",
    Attendance: "Keep track of today’s attendance across your school.",
    Grades: "Create assessments and record student scores.",
    "Report cards": "Review term results and attendance for each student.",
    Fees: "Stay on top of school finances and fee collection.",
    Announcements: "Share important updates with parents and guardians.",
    "Message moderation": "Review messages flagged by teachers and parents.",
    Timetable: "Schedule classes, teachers, subjects, and rooms.",
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar${menuOpen ? " sidebar-open" : ""}`}>
        <div className="sidebar-top">
          <Logo />
          <button className="icon-button mobile-close" aria-label="Close menu" onClick={() => setMenuOpen(false)}><X size={18} /></button>
        </div>

        <button className="school-switcher">
          <span className="school-avatar">N</span>
          <span className="school-copy"><strong>Northstar Academy</strong><small>School workspace</small></span>
          <ChevronDown size={16} className="muted-icon" />
        </button>

        <div className="nav-label">WORKSPACE</div>
        <nav className="main-nav" aria-label="Main navigation">
          {navItems.map(({ label, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item${activeSection === label ? " nav-item-active" : ""}`}
              onClick={() => {
                if (
                  label === "Students"
                  || label === "Attendance"
                  || label === "Grades"
                  || label === "Report cards"
                ) {
                  router.push(
                    label === "Students"
                      ? "/students"
                      : label === "Attendance"
                        ? "/attendance"
                        : label === "Grades"
                          ? "/grades"
                          : "/report-cards",
                  );
                  return;
                }
                setActiveSection(label);
                setMenuOpen(false);
              }}
            >
              <Icon size={18} strokeWidth={1.8} /><span>{label}</span>
              {label === "Attendance" && <span className="nav-count">12</span>}
            </button>
          ))}
        </nav>

        <div className="nav-label management-label">MANAGEMENT</div>
        <nav className="main-nav" aria-label="Management navigation">
          {isSchoolAdmin && (
            <button className="nav-item" onClick={() => router.push("/staff")}>
              <UserRound size={18} strokeWidth={1.8} /><span>Staff records</span>
            </button>
          )}
          {isSchoolAdmin && (
            <button
              className={`nav-item${activeSection === "Message moderation" ? " nav-item-active" : ""}`}
              onClick={() => {
                setActiveSection("Message moderation");
                setMenuOpen(false);
              }}
            >
              <MessageCircle size={18} strokeWidth={1.8} /><span>Message moderation</span>
            </button>
          )}
          {isSchoolAdmin && (
            <button
              className={`nav-item${activeSection === "Announcements" ? " nav-item-active" : ""}`}
              onClick={() => {
                setActiveSection("Announcements");
                setMenuOpen(false);
              }}
            >
              <Megaphone size={18} strokeWidth={1.8} /><span>Announcements</span>
            </button>
          )}
          <button
            className={`nav-item${activeSection === "Analytics" ? " nav-item-active" : ""}`}
            onClick={() => {
              setActiveSection("Analytics");
              setMenuOpen(false);
            }}
          >
            <BarChart3 size={18} strokeWidth={1.8} /><span>Analytics</span>
          </button>
          <button className="nav-item" onClick={() => {
            if (isSchoolAdmin) setActiveSection("Timetable");
            else setNotice("Calendar is coming soon.");
          }}><CalendarDays size={18} strokeWidth={1.8} /><span>Calendar</span></button>
        </nav>

        <div className="sidebar-spacer" />
        <div className="help-card">
          <div className="help-icon"><Sparkles size={16} /></div>
          <strong>Need a hand?</strong>
          <p>Our school success team is always happy to help.</p>
          <button onClick={() => setNotice("We’ll be in touch with support options shortly.")}>Get support <ArrowRight size={14} /></button>
        </div>
        <button className="nav-item settings-link" onClick={() => setNotice("Settings are coming soon.")}><Settings size={18} strokeWidth={1.8} /><span>Settings</span></button>
        <div className="profile">
          <Avatar initials="AM" color="lilac" />
          <div className="profile-copy"><strong>Alex Morgan</strong><span>School administrator</span></div>
          <button className="icon-button profile-menu" aria-label="Profile menu" onClick={() => setNotice("Profile settings are coming soon.")}><MoreHorizontal size={19} /></button>
        </div>
      </aside>

      {menuOpen && <button className="mobile-backdrop" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}

      <main className="main-content">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="Open menu" onClick={() => setMenuOpen(true)}><Menu size={20} /></button>
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14} /><strong>{activeSection}</strong></div>
          <div className="topbar-actions">
            <span className="today-label">{todayLabel}</span>
            {activeSection !== "Overview" && activeSection !== "Analytics" && activeSection !== "Fees" && activeSection !== "Announcements" && activeSection !== "Message moderation" && activeSection !== "Timetable" && <span className="demo-pill">DEMO DATA</span>}
            <button className={`icon-button notification-button${showNotifications ? " pressed" : ""}`} aria-label="Notifications" onClick={() => setShowNotifications(!showNotifications)}>
              <Bell size={18} /><span className="notification-dot" />
            </button>
            {showNotifications && (
              <div className="notification-popover">
                <div className="popover-heading"><strong>Notifications</strong><button onClick={() => setShowNotifications(false)} aria-label="Close notifications"><X size={16} /></button></div>
                <p><span className="notification-mini purple-mini"><Users size={14} /></span><span><b>New enrollment</b><small>Emma Johnson joined Grade 6 · 14 min ago</small></span></p>
                <p><span className="notification-mini green-mini"><Check size={14} /></span><span><b>Fee payment received</b><small>Payment confirmed for James Wilson · 1 hr ago</small></span></p>
                <button className="all-notifications" onClick={() => setShowNotifications(false)}>View all notifications <ArrowRight size={14} /></button>
              </div>
            )}
          </div>
        </header>

        <div className="page-wrap">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="sparkle">✳</span> {activeSection === "Fees" ? "BILLING MANAGEMENT" : activeSection === "Analytics" || activeSection === "Overview" ? "SCHOOL PERFORMANCE" : "YOUR SCHOOL AT A GLANCE"}</div>
              <h1>{sectionTitle[activeSection]}<span className="greeting-wave">✳</span></h1>
              <p className="page-description">{sectionDescription[activeSection]}</p>
            </div>
            {activeSection !== "Overview" && activeSection !== "Analytics" && activeSection !== "Fees" && activeSection !== "Announcements" && activeSection !== "Message moderation" && activeSection !== "Timetable" && <div className="welcome-actions">
              <button className="button button-secondary" onClick={() => setNotice("Your school report is being prepared.")}><Download size={16} /> Export report</button>
              <button className="button button-primary" onClick={() => router.push("/students")}><Plus size={17} /> Add student</button>
            </div>}
          </section>

          {activeSection === "Overview" || activeSection === "Analytics" ? <AnalyticsWorkspace /> : activeSection === "Fees" ? <BillingWorkspace /> : activeSection === "Announcements" ? <AnnouncementWorkspace /> : activeSection === "Message moderation" ? <MessageModerationWorkspace /> : activeSection === "Timetable" ? <TimetableWorkspace /> : <>
          <section className="stat-grid" aria-label="School statistics">
            <article className="stat-card">
              <div className="stat-heading"><span>Total students</span><span className="stat-icon violet-icon"><Users size={17} /></span></div>
              <div className="stat-value">1,284</div>
              <div className="stat-foot"><span className="trend-positive"><ArrowUpRight size={14} /> 8.2%</span><span>vs. last term</span><span className="stat-foot-right">Across 32 classes</span></div>
              <div className="mini-bars bars-purple">{[26, 40, 35, 54, 46, 67, 59, 76, 63, 91, 73, 100].map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div>
            </article>
            <article className="stat-card">
              <div className="stat-heading"><span>Attendance today</span><span className="stat-icon green-icon"><ClipboardCheck size={17} /></span></div>
              <div className="stat-value">94.8<span className="value-unit">%</span></div>
              <div className="stat-foot"><span className="trend-positive"><ArrowUpRight size={14} /> 2.4%</span><span>vs. last week</span><span className="stat-foot-right">1,217 present</span></div>
              <div className="mini-bars bars-green">{[58, 45, 70, 52, 80, 66, 94, 58, 74, 62, 100, 84].map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div>
            </article>
            <article className="stat-card">
              <div className="stat-heading"><span>Fees collected</span><span className="stat-icon orange-icon"><Wallet size={17} /></span></div>
              <div className="stat-value"><span className="currency">$</span>86,420</div>
              <div className="stat-foot"><span className="trend-positive"><ArrowUpRight size={14} /> 12.5%</span><span>this term</span><span className="stat-foot-right">of $104,000 goal</span></div>
              <div className="mini-bars bars-orange">{[35, 52, 43, 62, 54, 70, 58, 77, 65, 86, 74, 100].map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div>
            </article>
            <article className="stat-card">
              <div className="stat-heading"><span>Teaching staff</span><span className="stat-icon blue-icon"><GraduationCap size={17} /></span></div>
              <div className="stat-value">68</div>
              <div className="stat-foot"><span className="staff-avatars"><Avatar initials="JM" color="peach" small /><Avatar initials="SK" color="mint" small /><Avatar initials="+4" color="slate" small /></span><span>Active this term</span><span className="stat-foot-right">View staff <ArrowRight size={13} /></span></div>
              <div className="staff-progress"><span /></div>
            </article>
          </section>

          <section className="content-grid">
            <article className="panel attendance-panel">
              <div className="panel-header">
                <div><div className="panel-title-row"><h2>Attendance overview</h2><span className="live-pill sample-pill">Sample</span></div><p>Daily attendance across all grades</p></div>
                <button className="select-button" onClick={() => setNotice("Showing attendance for this week.")}>This week <ChevronDown size={14} /></button>
              </div>
              <div className="chart-summary"><strong>94.8%</strong><span><ArrowUpRight size={14} /> 2.4%</span><small>vs. previous week</small></div>
              <div className="chart">
                <div className="chart-y-labels"><span>100%</span><span>75%</span><span>50%</span><span>25%</span><span>0%</span></div>
                <div className="chart-main">
                  <div className="chart-gridlines"><i /><i /><i /><i /><i /></div>
                  <svg className="chart-svg" viewBox="0 0 640 150" preserveAspectRatio="none" role="img" aria-label="Attendance rose throughout the week, reaching 95 percent">
                    <defs><linearGradient id="chartFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#7761df" stopOpacity=".17" /><stop offset="100%" stopColor="#7761df" stopOpacity="0" /></linearGradient></defs>
                    <path d="M0 98 C35 88 47 103 80 82 S126 74 160 84 S205 73 240 60 S286 69 320 55 S364 66 400 48 S447 62 480 36 S528 48 560 28 S610 38 640 17 L640 150 L0 150 Z" fill="url(#chartFill)" />
                    <path d="M0 98 C35 88 47 103 80 82 S126 74 160 84 S205 73 240 60 S286 69 320 55 S364 66 400 48 S447 62 480 36 S528 48 560 28 S610 38 640 17" fill="none" stroke="#7660dc" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
                    <circle cx="640" cy="17" r="4" fill="#7660dc" stroke="white" strokeWidth="2" vectorEffect="non-scaling-stroke" />
                  </svg>
                  <div className="chart-x-labels"><span>Mon, 21</span><span>Tue, 22</span><span>Wed, 23</span><span>Thu, 24</span><span>Fri, 25</span><span>Today</span></div>
                </div>
              </div>
              <div className="chart-legend"><span><i className="legend-dot violet-dot" /> This week</span><span><i className="legend-dot gray-dot" /> Last week</span></div>
            </article>

            <article className="panel events-panel">
              <div className="panel-header">
                <div><h2>Upcoming events</h2><p>What’s coming up at your school</p></div>
                <button className="text-button" onClick={() => setNotice("Opening the school calendar.")}>View calendar <ArrowRight size={14} /></button>
              </div>
              <div className="event-list">
                <div className="event-item"><div className="event-date purple-date"><strong>31</strong><span>OCT</span></div><div className="event-copy"><strong>Parent–teacher conferences</strong><span><Clock3 size={13} /> 9:00 AM – 3:00 PM</span></div><span className="event-tag tag-purple">School-wide</span><button aria-label="More options" onClick={() => setNotice("Event options opened.")}><Ellipsis size={18} /></button></div>
                <div className="event-item"><div className="event-date orange-date"><strong>04</strong><span>NOV</span></div><div className="event-copy"><strong>Grade 8 science fair</strong><span><Clock3 size={13} /> 10:30 AM – 1:00 PM</span></div><span className="event-tag tag-orange">Academic</span><button aria-label="More options" onClick={() => setNotice("Event options opened.")}><Ellipsis size={18} /></button></div>
                <div className="event-item"><div className="event-date green-date"><strong>08</strong><span>NOV</span></div><div className="event-copy"><strong>Autumn sports day</strong><span><Clock3 size={13} /> 8:00 AM – 2:30 PM</span></div><span className="event-tag tag-green">Community</span><button aria-label="More options" onClick={() => setNotice("Event options opened.")}><Ellipsis size={18} /></button></div>
              </div>
              <button className="add-event-button" onClick={() => setNotice("Create event form will be available soon.")}><Plus size={15} /> Add event to calendar</button>
            </article>
          </section>

          <section className="panel students-panel">
            <div className="panel-header students-header">
              <div><div className="panel-title-row"><h2>Student attendance</h2><span className="count-pill">1,284 students</span></div><p>A quick look at today’s classroom check-ins</p></div>
              <div className="table-actions">
                <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search students..." aria-label="Search students" /><kbd>⌘ K</kbd></label>
                <button className="select-button filter-button" onClick={() => setNotice("Filters are up to date.")}><SlidersHorizontal size={15} /> Filter</button>
                <button className="icon-button more-button" aria-label="More table options" onClick={() => setNotice("More table options are coming soon.")}><Ellipsis size={18} /></button>
              </div>
            </div>
            <div className="table-scroll">
              <table>
                <thead><tr><th><input type="checkbox" aria-label="Select all students" /></th><th>STUDENT NAME <ChevronDown size={12} /></th><th>CLASS</th><th>ATTENDANCE</th><th>CHECK-IN</th><th /></tr></thead>
                <tbody>
                  {filteredStudents.map((student, index) => (
                    <tr key={student.name}>
                      <td><input type="checkbox" aria-label={`Select ${student.name}`} /></td>
                      <td><div className="student-cell"><Avatar initials={student.initials} color={student.color} /><strong>{student.name}</strong></div></td>
                      <td><span className="class-name">{student.grade}</span></td>
                      <td><span className={`status status-${student.attendance.toLowerCase()}`}><i />{student.attendance}</span></td>
                      <td><span className="check-in">{student.attendance === "Absent" ? "—" : index === 2 ? "8:17 AM" : `${index + 7}:0${index} AM`}</span></td>
                      <td><button className="row-action" aria-label={`Update ${student.name} attendance`} onClick={() => setAttendance(student.name)}><Ellipsis size={18} /></button></td>
                    </tr>
                  ))}
                  {filteredStudents.length === 0 && <tr><td colSpan={6} className="empty-row">No students match “{query}”. Try another search.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="table-footer"><span>Showing <strong>{filteredStudents.length === 0 ? 0 : 1}–{filteredStudents.length}</strong> of <strong>1,284</strong> students</span><div className="pagination"><button aria-label="Previous page" onClick={() => setNotice("You’re on the first page.")}><ChevronLeft size={16} /></button><button className="page-active">1</button><button onClick={() => setNotice("Page 2 is coming soon.")}>2</button><button onClick={() => setNotice("Page 3 is coming soon.")}>3</button><span>…</span><button onClick={() => setNotice("Last page is coming soon.")}>26</button><button aria-label="Next page" onClick={() => setNotice("Page 2 is coming soon.")}><ChevronRight size={16} /></button></div></div>
          </section>

          <footer className="page-footer"><span>© {new Date().getFullYear()} Northstar School Management</span><button onClick={() => setNotice("Help center is coming soon.")}><CircleHelp size={14} /> Help center</button><span className="footer-status">Demo workspace · sample data only</span></footer>
          </>}
        </div>
      </main>
      <FeedbackModal message={notice} tone="info" onClose={() => setNotice("")} />
    </div>
  );
}

export default App;
