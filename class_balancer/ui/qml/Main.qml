import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1280
    height: 820
    visible: true
    title: "Mosaicly - שיבוץ חכם"
    color: "#f6f8fb"
    font.family: "Segoe UI"
    font.pixelSize: 14
    palette.window: "#f6f8fb"
    palette.base: "#ffffff"
    palette.text: "#172033"
    palette.button: "#ffffff"
    palette.buttonText: "#172033"
    palette.highlight: "#0f766e"
    palette.highlightedText: "#ffffff"
    LayoutMirroring.enabled: true
    LayoutMirroring.childrenInherit: true

    property int pageIndex: 0
    property var previewData: ({ headers: [], rows: [], sheet_names: [], row_count: 0 })
    property var mappingData: []
    property var currentProjectData: ({})
    property var dashboard: ({ has_assignment: false })
    property var classes: []
    property var recentProjectsData: []
    property var studentsData: []
    property var validationIssuesData: []
    property var studentsInClass: []
    property var selectedDetails: ({})
    property int selectedClassId: 0
    property var selectedClassStats: ({})
    property int selectedStudentId: 0
    property bool studentDetailsLoading: false
    property string studentDetailsError: ""
    property string importFileUrl: ""
    property var aiSettingsData: ({ providers: [] })
    property var qualityData: ({ has_assignment: false })
    property var conflictsData: ({ status: "not_loaded", message: "", conflicts: [], suggested_actions: [], action_candidates: [] })
    property var aiActionData: ({ status: "not_run", actions: [], message: "" })
    property var aiRuleData: ({ status: "not_run", recommendation: {}, providers: [], message: "" })
    property var compareData: ({})
    property var aiReviewData: ({ status: "not_run", providers: [], best: {} })
    property var friendshipDiagnosticData: ({ status: "not_run", message: "", result: {} })
    property bool projectAiAllowed: false
    property int pendingDeleteProjectId: 0
    property string pendingDeleteProjectName: ""
    property int studentCount: 0
    property int versionCount: 0
    property string studentSearchText: ""
    property string studentFilterMode: "הכל"
    property bool showClassColumn: true
    property bool showGenderColumn: true
    property bool showSchoolColumn: true
    property bool showGradeColumn: true
    property bool showMathColumn: false
    property bool showEnglishColumn: false
    property bool showHebrewColumn: false
    property bool showBehaviorColumn: true
    property bool showFriendsColumn: true
    property bool showRequestedByColumn: false
    property bool showNotesColumn: false
    property bool showConstraintsColumn: false
    property bool mappingAiBusy: false
    property bool reportAiBusy: false
    property bool settingsAiBusy: false
    property string mappingAiOutput: ""
    property string reportAiOutput: ""
    property string conflictAiOutput: ""
    property string settingsAiOutput: ""
    property var bulkConstraintStudents: []
    property var bulkConstraintClasses: []
    property string bulkConstraintSearch: ""
    property bool developerUnlocked: false
    property string developerPasswordError: ""

    function countIssues(severity) {
        var issues = validationIssuesData || []
        var count = 0
        for (var i = 0; i < issues.length; i++) {
            if (issues[i].severity === severity) count++
        }
        return count
    }

    function dashboardPenalty(label) {
        if (!dashboard.has_assignment || !dashboard.score.penalties) return ""
        return dashboard.score.penalties[label] || 0
    }

    function unlockDeveloperSettings(password) {
        if (password === "7080") {
            developerUnlocked = true
            developerPasswordInput.text = ""
            developerPasswordError = ""
            developerPasswordDialog.close()
        } else {
            developerPasswordError = "סיסמה שגויה."
        }
    }

    function hasProject() {
        return currentProjectData && currentProjectData.id !== undefined && currentProjectData.id !== 0
    }

    function currentProjectName() {
        var project = currentProjectData
        if (!project || project.id === undefined || project.id === 0) return "לא נבחר פרויקט"
        return project.name || "פרויקט ללא שם"
    }

    function hasPreview() {
        return previewData && previewData.row_count > 0 && previewData.headers.length > 0
    }

    function hasAssignment() {
        return dashboard && dashboard.has_assignment
    }

    function refreshAll() {
        previewData = bridge.previewData()
        mappingData = bridge.mappingRows()
        currentProjectData = bridge.currentProject()
        recentProjectsData = bridge.recentProjects()
        dashboard = bridge.dashboardData()
        classes = bridge.classList()
        studentsData = bridge.studentList()
        validationIssuesData = bridge.validationIssues()
        studentCount = studentsData.length
        versionCount = dashboard.has_assignment && dashboard.versions ? dashboard.versions.length : 0
        aiReviewData = bridge.aiReviewData()
        friendshipDiagnosticData = bridge.friendshipDiagnosticData()
        aiSettingsData = bridge.aiSettings()
        projectAiAllowed = bridge.projectAllowsExternalAi()
        aiActionData = bridge.aiActionSuggestionsData()
        aiRuleData = bridge.aiRuleRecommendationData()
        if (classes.length === 0) {
            selectedClassId = 0
        }
        if (selectedClassId !== 0) {
            var selectedClassExists = false
            for (var classIndex = 0; classIndex < classes.length; classIndex++) {
                if (classes[classIndex].id === selectedClassId) selectedClassExists = true
            }
            if (!selectedClassExists) selectedClassId = 0
        }
        studentsInClass = rowsForClass(selectedClassId)
        selectedClassStats = classStatsFor(selectedClassId)
        if (selectedStudentId !== 0) {
            studentDetailsLoading = true
            studentDetailsError = ""
            bridge.studentDetailsAsync(selectedStudentId)
        }
        refreshPageData()
    }

    function refreshPageData() {
        if (pageIndex === 7) {
            conflictsData = bridge.conflictsReport()
            if (!conflictsData.status || conflictsData.status === "not_loaded") {
                bridge.loadConflictsReportAsync()
                conflictsData = bridge.conflictsReport()
            }
        } else if (pageIndex === 8) {
            qualityData = bridge.qualityReport()
        }
    }

    function resetProjectScopedSelection() {
        selectedClassId = 0
        selectedClassStats = ({})
        studentsInClass = []
        selectedStudentId = 0
        selectedDetails = ({})
        studentDetailsLoading = false
        studentDetailsError = ""
        qualityData = ({ has_assignment: false })
        conflictsData = ({ status: "not_loaded", message: "", conflicts: [], suggested_actions: [], action_candidates: [] })
        compareData = ({})
        friendshipDiagnosticData = ({ status: "not_run", message: "", result: {} })
    }

    function rowsForClass(classId) {
        var rows = dashboard && dashboard.rows ? dashboard.rows : []
        if (parseInt(classId) <= 0) return rows
        var filtered = []
        for (var i = 0; i < rows.length; i++) {
            if (parseInt(rows[i].class_id) === parseInt(classId)) filtered.push(rows[i])
        }
        return filtered
    }

    function visibleStudents() {
        var rows = studentsInClass || []
        var filtered = []
        var search = studentSearchText.toLowerCase()
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i]
            var name = String(row.full_name || row.first_name || "").toLowerCase()
            var code = String(row.internal_code || "").toLowerCase()
            var className = String(row.class_name || "").toLowerCase()
            var friends = String(row.requested_friends || row.requested_by || "").toLowerCase()
            var include = search.length === 0 || name.indexOf(search) >= 0 || code.indexOf(search) >= 0 || className.indexOf(search) >= 0 || friends.indexOf(search) >= 0
            if (studentFilterMode === "נעולים") include = include && row.locked_manually
            if (studentFilterMode === "שונו ידנית") include = include && row.changed_manually
            if (studentFilterMode === "בנים") include = include && row.gender === "בן"
            if (studentFilterMode === "בנות") include = include && row.gender === "בת"
            if (studentFilterMode === "ללא חבר") include = include && !row.got_friend
            if (include) filtered.push(row)
        }
        return filtered
    }

    function chooseClass(classId) {
        selectedClassId = classId
        studentsInClass = rowsForClass(classId)
        selectedClassStats = classStatsFor(classId)
    }

    function classStatsFor(classId) {
        if (!dashboard.has_assignment || !dashboard.score || !dashboard.score.class_stats) return ({})
        for (var i = 0; i < dashboard.score.class_stats.length; i++) {
            if (dashboard.score.class_stats[i].class_id === classId) return dashboard.score.class_stats[i]
        }
        return ({})
    }

    function classIndexById(classId) {
        for (var i = 0; i < classes.length; i++) {
            if (classes[i].id === classId) return i
        }
        return 0
    }

    function classIdAt(index) {
        if (!classes || index < 0 || index >= classes.length) return 0
        return parseInt(classes[index].id)
    }

    function backendLabel(value) {
        if (value === "local") return "מקומי"
        if (value === "exact") return "מדויק"
        return "אוטומטי"
    }

    function backendValue(label) {
        if (label === "מקומי") return "local"
        if (label === "מדויק") return "exact"
        return "auto"
    }

    function backendIndex(value) {
        var label = backendLabel(value)
        var labels = ["אוטומטי", "מקומי", "מדויק"]
        return Math.max(0, labels.indexOf(label))
    }

    function openClassDetails(classId) {
        chooseClass(classId)
        classDetailsDialog.open()
    }

    function chooseStudent(studentId) {
        selectedStudentId = studentId
        selectedDetails = ({})
        studentDetailsLoading = true
        studentDetailsError = ""
        bridge.studentDetailsAsync(studentId)
    }

    function studentDetailsReady() {
        return selectedStudentId !== 0 && !studentDetailsLoading && studentDetailsError.length === 0 && !!selectedDetails.student
    }

    function selectedClassName() {
        if (selectedClassId === 0) return "כל הכיתות"
        for (var i = 0; i < classes.length; i++) {
            if (classes[i].id === selectedClassId) return classes[i].name
        }
        return ""
    }

    function formatValue(value, emptyText) {
        if (value === undefined || value === null || value === "") return emptyText || "-"
        return String(value)
    }

    function counterText(counter) {
        if (!counter) return "-"
        var parts = []
        for (var key in counter) {
            if (counter[key] !== undefined && counter[key] !== null && counter[key] !== 0) {
                parts.push(key + ": " + counter[key])
            }
        }
        return parts.length > 0 ? parts.join(" · ") : "-"
    }

    function percentageText(part, total) {
        var cleanTotal = Number(total || 0)
        if (cleanTotal <= 0) return "-"
        return Math.round((Number(part || 0) / cleanTotal) * 100) + "%"
    }

    function containsValue(list, value) {
        if (!list) return false
        for (var i = 0; i < list.length; i++) {
            if (String(list[i]) === String(value)) return true
        }
        return false
    }

    function toggledList(list, value) {
        var result = []
        var found = false
        for (var i = 0; i < (list || []).length; i++) {
            if (String(list[i]) === String(value)) {
                found = true
            } else {
                result.push(list[i])
            }
        }
        if (!found) result.push(value)
        return result
    }

    function filteredStudentsForBulkConstraint() {
        var rows = studentsData || []
        var search = bulkConstraintSearch.toLowerCase()
        if (search.length === 0) return rows
        var filtered = []
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i]
            var name = String(row.full_name || row.first_name || "").toLowerCase()
            var code = String(row.internal_code || "").toLowerCase()
            var school = String(row.source_school || "").toLowerCase()
            if (name.indexOf(search) >= 0 || code.indexOf(search) >= 0 || school.indexOf(search) >= 0) filtered.push(row)
        }
        return filtered
    }

    function formatAiResult(result) {
        if (!result || !result.text) return ""
        var source = result.source || (result.used_ai ? "AI" : "local")
        var title = result.used_ai ? "תשובת AI" : "סיכום מקומי"
        return title + " · מקור: " + source + "\n\n" + result.text
    }

    function activeVersionIndex() {
        if (!hasAssignment() || !dashboard.versions) return -1
        for (var i = 0; i < dashboard.versions.length; i++) {
            if (dashboard.versions[i].is_active || (dashboard.version && dashboard.versions[i].id === dashboard.version.id)) return i
        }
        return dashboard.versions.length > 0 ? 0 : -1
    }

    function activeVersionName() {
        if (!hasAssignment() || !dashboard.version) return ""
        return dashboard.version.name || ""
    }

    function activeVersionScore() {
        if (!hasAssignment() || !dashboard.version) return ""
        return "ציון " + formatValue(dashboard.version.score_total)
    }

    function actionDeltaText(action) {
        var delta = Number(action && action.delta !== undefined ? action.delta : 0)
        if (delta > 0) return "+" + delta.toFixed(2)
        return delta.toFixed(2)
    }

    function actionDeltaColor(action) {
        var delta = Number(action && action.delta !== undefined ? action.delta : 0)
        if (delta > 0) return "#067647"
        if (delta < 0) return "#b42318"
        return "#475467"
    }

    function applyActionCandidate(action) {
        var result = bridge.applySuggestedAction(action)
        conflictAiOutput = result.ok ? result.message : ("לא ניתן לבצע את הפעולה: " + (result.message || "שגיאה לא ידועה"))
    }

    function friendSlotColor(slot) {
        if (!slot || !slot.requested) return "#eef4ff"
        if (slot.received) return "#ecfdf3"
        return "#fee4e2"
    }

    function friendSlotTextColor(slot) {
        if (!slot || !slot.requested) return "#175cd3"
        if (slot.received) return "#067647"
        return "#b42318"
    }

    function scoreText(value) {
        if (value === undefined || value === null || value === "") return "-"
        return String(Math.round(Number(value)))
    }

    function classStudentsCount(classId) {
        if (!dashboard.has_assignment || !dashboard.rows) return 0
        if (classId === 0) return dashboard.rows.length
        var count = 0
        for (var i = 0; i < dashboard.rows.length; i++) {
            if (parseInt(dashboard.rows[i].class_id) === parseInt(classId)) count++
        }
        return count
    }

    function penaltyLabel(key) {
        var labels = {
            "class_size": "גודל הכיתות",
            "gender_balance": "איזון מגדר",
            "academic_balance": "איזון ממוצע כללי",
            "subject_balance": "איזון מקצועות",
            "behavior_balance": "איזון התנהגות",
            "dominance_spread": "פיזור תלמידים דומיננטיים",
            "friendship": "בקשות חברים שלא מולאו",
            "source_school": "פיזור בתי ספר מקור",
            "hard_constraints": "כללים מחייבים שנשברו"
        }
        return labels[key] || key
    }

    function penaltyHelp(key) {
        var labels = {
            "class_size": "בודק אם יש כיתות גדולות או קטנות מדי ביחס ליעד. מספר נמוך יותר טוב.",
            "gender_balance": "בודק פערי בנים/בנות בין הכיתות, רק לפי נתוני מגדר ידועים.",
            "academic_balance": "בודק אם הממוצע הכללי דומה בין הכיתות.",
            "subject_balance": "בודק בנפרד מתמטיקה, אנגלית ועברית, כאשר הנתונים קיימים.",
            "behavior_balance": "בודק אם רמות התנהגות לא מרוכזות בכיתה אחת.",
            "dominance_spread": "בודק פיזור תלמידים שסומנו כדומיננטיים או מאתגרים.",
            "friendship": "בודק כמה תלמידים לא קיבלו אף חבר שביקשו.",
            "source_school": "בודק ריכוז או בדידות חברתית לפי בית ספר מקור.",
            "hard_constraints": "כלל מחייב הוא כלל שאסור לשבור. כאן צריך להיות 0."
        }
        return labels[key] || "מדד איכות פנימי. מספר נמוך יותר מצביע על פחות בעיות."
    }

    function moveStudentToClass(studentId, classId, lockAfterMove) {
        if (classId <= 0) return
        bridge.moveStudent(parseInt(studentId), parseInt(classId), lockAfterMove || false)
    }

    function providerStatus(provider) {
        var reviewProviders = aiReviewData && aiReviewData.providers ? aiReviewData.providers : []
        for (var i = 0; i < reviewProviders.length; i++) {
            if (reviewProviders[i].provider === provider.provider) {
                if (reviewProviders[i].ok) return "הצליח"
                if (reviewProviders[i].used) return "נכשל"
                return "לא הופעל"
            }
        }
        return provider.configured ? "מוגדר" : "חסר מפתח AI"
    }

    function providerBadgeColor(provider) {
        var status = providerStatus(provider)
        if (status === "הצליח" || status === "מוגדר") return "#ecfdf3"
        if (status === "נכשל") return "#fee4e2"
        return "#fff7ed"
    }

    function providerTextColor(provider) {
        var status = providerStatus(provider)
        if (status === "הצליח" || status === "מוגדר") return "#067647"
        if (status === "נכשל") return "#b42318"
        return "#b54708"
    }

    function friendshipDiagnosticResult() {
        if (!friendshipDiagnosticData || !friendshipDiagnosticData.result) return ({})
        return friendshipDiagnosticData.result
    }

    function friendshipDiagnosticStatusText() {
        var data = friendshipDiagnosticData || ({})
        if (data.status === "running") return "בודק..."
        if (data.status === "failed") return "נכשל: " + (data.message || "")
        if (data.status !== "done") return data.message || "לא הורצה"
        var result = friendshipDiagnosticResult()
        var title = "אין הוכחה חוסמת"
        if (result.legal_full_friend_coverage) {
            title = result.verdict === "proven_legal_100" ? "הוכח 100% חוקי" : "נמצא 100% חוקי"
        } else if (result.full_friend_coverage) {
            title = "נמצא 100% עם אילוצים שבורים"
        } else if (result.verdict === "provably_blocked_by_selected_rules" || result.verdict === "proven_blocked_by_selected_rules") {
            title = "הוכחה: אילוץ חוסם"
        } else if (result.verdict === "proven_hard_rules_infeasible") {
            title = "הוכחה: אין שיבוץ חוקי"
        }
        return title + " · " + String(result.satisfied_percent || 0) + "% · חסרים "
            + String(result.missing_count || 0) + "/" + String(result.total_with_requests || 0)
            + " · אילוצים שבורים " + String(result.hard_violation_count || 0)
    }

    function friendshipDiagnosticSummaryText() {
        var data = friendshipDiagnosticData || ({})
        if (data.status === "running") return "הבדיקה רצה ברקע. אפשר להמשיך לעבוד במסך."
        if (data.status === "failed") return data.message || ""
        if (data.status !== "done") return "בחרו אילוצים והריצו בדיקה כדי לראות אם 100% חברים אפשרי."
        var result = friendshipDiagnosticResult()
        var blockers = result.structural_blockers || ({})
        var lines = []
        if (result.summary) lines.push(result.summary)
        var columns = blockers.columns || []
        if (columns.length > 0) {
            var columnParts = []
            for (var i = 0; i < columns.length; i++) {
                columnParts.push(columns[i].label + " (" + columns[i].count + ")")
            }
            lines.push("עמודות חשודות: " + columnParts.join(" · "))
        }
        var globalBlockers = blockers.global_blockers || []
        for (var g = 0; g < globalBlockers.length && g < 2; g++) {
            lines.push(globalBlockers[g].message || globalBlockers[g].label)
        }
        var examples = blockers.examples || []
        if (examples.length > 0) {
            lines.push("דוגמה: " + examples[0].student_name + " · " + (examples[0].reason_labels || []).join(", "))
        }
        var stageText = friendshipDiagnosticStagesText()
        if (stageText.length > 0) lines.push(stageText)
        return lines.join("\n")
    }

    function friendshipDiagnosticStagesText() {
        var result = friendshipDiagnosticResult()
        var stages = result.stages || []
        var lines = []
        for (var i = 0; i < stages.length; i++) {
            var stage = stages[i]
            var missing = stage.missing_students || []
            var prefix = stage.label + ": "
            if (stage.proof_status === "exact_solver_unavailable") {
                lines.push(prefix + "אין מנוע הוכחה מדויק")
                continue
            }
            if ((stage.missing_count || 0) <= 0) {
                lines.push(prefix + "100%")
                continue
            }
            var names = []
            for (var j = 0; j < missing.length && j < 4; j++) {
                names.push(missing[j].student_name || String(missing[j].student_id || ""))
            }
            var more = missing.length > 4 ? " ועוד " + String(missing.length - 4) : ""
            lines.push(prefix + String(stage.missing_count || missing.length) + " בלי חבר: " + names.join(", ") + more)
        }
        return lines.join("\n")
    }

    function runFriendshipDiagnostic(options) {
        bridge.runFriendshipDiagnosticAsync(options)
    }

    function aiReviewStatusText() {
        if (!aiReviewData || !aiReviewData.status) return "AI עדיין לא הופעל."
        if (aiReviewData.status === "running") return "AI/ניתוח מקומי רץ עכשיו על סיכום נתונים אנונימי."
        if (aiReviewData.status === "ai_completed") return "AI הופעל: התקבל ניתוח מובנה משירותי AI זמינים."
        if (aiReviewData.status === "local_only") return "AI כבוי: נוצר ניתוח מקומי בלבד."
        if (aiReviewData.status === "ai_failed") return "AI לא הצליח לענות: מוצג ניתוח מקומי."
        if (aiReviewData.status === "skipped") return "AI לא הופעל כי הציון מעל הסף או שהבדיקה כבויה."
        return aiReviewData.text || "AI ממתין לשיבוץ."
    }

    function bestAiRecommendationText() {
        var best = aiReviewData && aiReviewData.best ? (aiReviewData.best.best_recommendation || {}) : {}
        if (best.title_he) return best.title_he + " · " + (best.reason_he || "")
        var recommendations = aiReviewData && aiReviewData.best ? (aiReviewData.best.recommendations || []) : []
        if (recommendations.length > 0) return recommendations[0].title_he + " · " + recommendations[0].reason_he
        return "אין המלצה זמינה כרגע."
    }

    function engineSourceText(score) {
        if (!score) return "לא ידוע"
        var engine = score.engine_note || {}
        var advisor = score.advisor_note || {}
        var source = engine.assignment_source === "exact_optimizer" ? "שיטת חישוב מדויקת" : "חיפוש מקומי"
        if (advisor.used && !advisor.kept_original) source += " + כוונון מקומי אוטומטי"
        if (engine.exact_optimizer_available && engine.assignment_source !== "exact_optimizer") source += " (השיטה המדויקת נבדקה, אבל החיפוש המקומי נתן תוצאה טובה יותר)"
        return source
    }

    function aiAssignmentText() {
        if (!aiReviewData || !aiReviewData.status) return "לא רץ"
        if (aiReviewData.used_ai) return "רץ לבדיקה/המלצות בלבד, לא קבע שיבוץ"
        if (aiReviewData.status === "running") return "בודק כעת דוח אנונימי"
        if (aiReviewData.status === "local_only") return "לא רץ, הוצג ניתוח מקומי"
        if (aiReviewData.status === "skipped") return "לא רץ כי הציון מעל הסף או כבוי"
        if (aiReviewData.status === "ai_failed") return "ניסיון AI נכשל, נשמר ניתוח מקומי"
        return "לא קובע שיבוץ"
    }

    Component.onCompleted: refreshAll()

    onPageIndexChanged: refreshPageData()

    Connections {
        target: bridge
        function onDataChanged() { refreshAll() }
        function onPreviewChanged() {
            previewData = bridge.previewData()
            mappingData = bridge.mappingRows()
        }
        function onCurrentProjectChanged() { resetProjectScopedSelection() }
        function onStudentDetailsLoaded(studentId, details) {
            if (parseInt(studentId) !== parseInt(selectedStudentId)) return
            studentDetailsLoading = false
            studentDetailsError = ""
            selectedDetails = details || ({})
            if (!selectedDetails.student) {
                selectedStudentId = 0
                selectedDetails = ({})
            }
        }
        function onStudentDetailsFailed(studentId, message) {
            if (parseInt(studentId) !== parseInt(selectedStudentId)) return
            studentDetailsLoading = false
            studentDetailsError = message || "לא ניתן לטעון את פרטי התלמיד."
        }
        function onAiReviewChanged() {
            aiReviewData = bridge.aiReviewData()
        }
        function onFriendshipDiagnosticChanged() {
            friendshipDiagnosticData = bridge.friendshipDiagnosticData()
        }
        function onConflictsReportChanged() {
            conflictsData = bridge.conflictsReport()
        }
        function onAiActionSuggestionsChanged() {
            aiActionData = bridge.aiActionSuggestionsData()
        }
        function onAiRuleRecommendationChanged() {
            aiRuleData = bridge.aiRuleRecommendationData()
        }
        function onAiConnectionTestFinished(result) {
            settingsAiBusy = false
            settingsAiOutput = JSON.stringify(result, null, 2)
            aiSettingsData = bridge.aiSettings()
            aiReviewData = bridge.aiReviewData()
        }
        function onAssignmentFinished() {
            pageIndex = 6
        }
        function onAssistantFinished(requestId) {
            var result = bridge.assistantResult(requestId)
            if (requestId === "mapping") {
                mappingAiBusy = false
                mappingAiOutput = formatAiResult(result)
            } else if (requestId === "report") {
                reportAiBusy = false
                reportAiOutput = formatAiResult(result)
            } else if (requestId === "settings") {
                settingsAiBusy = false
                settingsAiOutput = formatAiResult(result)
            }
        }
    }

    FileDialog {
        id: importDialog
        title: "בחירת קובץ תלמידים"
        fileMode: FileDialog.OpenFile
        nameFilters: ["קבצי Excel/CSV (*.xlsx *.csv)", "כל הקבצים (*)"]
        onAccepted: {
            importFileUrl = selectedFile.toString()
            bridge.previewFileAsync(importFileUrl, "")
            pageIndex = 2
        }
    }

    FileDialog {
        id: exportDialog
        title: "שמירת קובץ Excel"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Excel (*.xlsx)"]
        onAccepted: {
            bridge.exportExcelAsync(selectedFile.toString())
        }
    }

    FileDialog {
        id: validationExportDialog
        title: "שמירת שגיאות בדיקת נתונים"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Excel (*.xlsx)"]
        onAccepted: {
            bridge.exportValidationIssuesExcelAsync(selectedFile.toString())
        }
    }

    Dialog {
        id: deleteProjectDialog
        modal: true
        title: "מחיקת פרויקט"
        standardButtons: Dialog.NoButton
        width: Math.min(460, window.width - 48)
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - height) / 2)
        Label {
            text: "למחוק את הפרויקט \"" + pendingDeleteProjectName + "\"? הפעולה תמחק גם תלמידים, כיתות וגרסאות שיבוץ של הפרויקט."
            wrapMode: Text.WordWrap
            width: parent ? parent.width : 420
            color: "#172033"
        }
        footer: RowLayout {
            spacing: 10
            Button {
                text: "ביטול"
                Layout.fillWidth: true
                onClicked: deleteProjectDialog.close()
            }
            Button {
                text: "מחיקה"
                highlighted: true
                Layout.fillWidth: true
                onClicked: {
                    if (pendingDeleteProjectId > 0) {
                        bridge.deleteProject(pendingDeleteProjectId)
                        pendingDeleteProjectId = 0
                        pendingDeleteProjectName = ""
                    }
                    deleteProjectDialog.close()
                }
            }
        }
    }

    Dialog {
        id: developerPasswordDialog
        modal: true
        title: "כניסת מפתח"
        standardButtons: Dialog.NoButton
        width: Math.min(420, window.width - 48)
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - height) / 2)
        ColumnLayout {
            width: parent ? parent.width : 380
            spacing: 12
            Label {
                text: "הגדרות מפתח משפיעות על זמן הרצה, איטרציות ומשקלי ניקוד."
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                color: "#475467"
            }
            TextField {
                id: developerPasswordInput
                echoMode: TextInput.Password
                placeholderText: "סיסמה"
                Layout.fillWidth: true
                onAccepted: unlockDeveloperSettings(developerPasswordInput.text)
            }
            Label {
                visible: developerPasswordError.length > 0
                text: developerPasswordError
                color: "#b42318"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        footer: RowLayout {
            spacing: 10
            Button {
                text: "ביטול"
                Layout.fillWidth: true
                onClicked: developerPasswordDialog.close()
            }
            Button {
                id: developerUnlockButton
                text: "כניסה"
                highlighted: true
                Layout.fillWidth: true
                onClicked: unlockDeveloperSettings(developerPasswordInput.text)
            }
        }
        onOpened: {
            developerPasswordInput.text = ""
            developerPasswordError = ""
            developerPasswordInput.forceActiveFocus()
        }
    }

    Dialog {
        id: classDetailsDialog
        modal: true
        title: "נתוני כיתה: " + (selectedClassStats.name || "")
        standardButtons: Dialog.NoButton
        width: Math.min(980, window.width - 48)
        height: Math.min(720, window.height - 72)
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - height) / 2)
        ScrollView {
            anchors.fill: parent
            clip: true
            ColumnLayout {
                width: classDetailsDialog.availableWidth
                spacing: 16

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    ClassMetricTile {
                        title: "תלמידים"
                        value: formatValue(selectedClassStats.size, "0")
                        detail: "בכיתה"
                        accent: "#175cd3"
                    }
                    ClassMetricTile {
                        title: "מגדר"
                        value: formatValue(selectedClassStats.boys, "0") + " / " + formatValue(selectedClassStats.girls, "0")
                        detail: "בנים / בנות"
                        accent: "#0f766e"
                    }
                    ClassMetricTile {
                        title: "ממוצע"
                        value: formatValue(selectedClassStats.avg_grade)
                        detail: "כללי"
                        accent: "#7e22ce"
                    }
                    ClassMetricTile {
                        title: "ציון כיתה"
                        value: formatValue(selectedClassStats.quality_score)
                        detail: selectedClassStats.quality_summary || "מדד פנימי"
                        accent: selectedClassStats.quality_score >= 85 ? "#067647" : (selectedClassStats.quality_score >= 70 ? "#b54708" : "#d92d20")
                    }
                    ClassMetricTile {
                        title: "חברים"
                        value: percentageText(selectedClassStats.friends_satisfied, selectedClassStats.total_with_friend_requests)
                        detail: formatValue(selectedClassStats.friends_satisfied, "0") + " מולאו, " + formatValue(selectedClassStats.friends_missing, "0") + " חסרות"
                        accent: selectedClassStats.friends_missing > 0 ? "#b54708" : "#067647"
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 12
                    rowSpacing: 12
                    ClassInfoBlock {
                        title: "ציונים"
                        body: "כללי: " + formatValue(selectedClassStats.avg_grade) + "\nמתמטיקה: " + formatValue(selectedClassStats.math_avg) + " · אנגלית: " + formatValue(selectedClassStats.english_avg) + " · עברית: " + formatValue(selectedClassStats.hebrew_avg) + "\nטווחים: " + counterText(selectedClassStats.grade_bands)
                    }
                    ClassInfoBlock {
                        title: "התנהגות ודומיננטיות"
                        body: "התנהגות: " + counterText(selectedClassStats.behavior_counts) + "\nממוצע התנהגות: " + formatValue(selectedClassStats.avg_behavior) + "\nדומיננטיות: סה\"כ " + formatValue(selectedClassStats.dominance_total) + " · ממוצע " + formatValue(selectedClassStats.dominance_average)
                    }
                    ClassInfoBlock {
                        title: "בתי ספר מקור"
                        body: counterText(selectedClassStats.schools)
                    }
                    ClassInfoBlock {
                        title: "נתונים חסרים"
                        body: "מגדר: " + formatValue(selectedClassStats.missing_gender_count, "0") + " · ממוצע: " + formatValue(selectedClassStats.missing_grade_count, "0") + "\nמתמטיקה: " + formatValue(selectedClassStats.missing_math_count, "0") + " · אנגלית: " + formatValue(selectedClassStats.missing_english_count, "0") + " · עברית: " + formatValue(selectedClassStats.missing_hebrew_count, "0") + "\nהתנהגות: " + formatValue(selectedClassStats.missing_behavior_count, "0") + " · דומיננטיות: " + formatValue(selectedClassStats.missing_dominance_count, "0")
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 330
                    radius: 8
                    color: "#f8fafc"
                    border.color: "#dde5ef"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "תלמידי הכיתה"; font.bold: true; font.pixelSize: 16; color: "#172033"; Layout.fillWidth: true }
                            MiniBadge { textValue: String((studentsInClass || []).length) + " תלמידים"; badgeColor: "#eef4ff"; textColor: "#175cd3" }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34
                            color: "#eef4ff"
                            border.color: "#c7d7fe"
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 10
                                Label { text: "שם"; font.bold: true; Layout.preferredWidth: 210; color: "#172033"; elide: Text.ElideRight }
                                Label { text: "מגדר"; font.bold: true; Layout.preferredWidth: 64; color: "#172033"; elide: Text.ElideRight }
                                Label { text: "בית ספר"; font.bold: true; Layout.preferredWidth: 140; color: "#172033"; elide: Text.ElideRight }
                                Label { text: "ממוצע"; font.bold: true; Layout.preferredWidth: 86; color: "#172033"; elide: Text.ElideRight }
                                Label { text: "חברים"; font.bold: true; Layout.fillWidth: true; color: "#172033"; elide: Text.ElideRight }
                            }
                        }
                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: studentsInClass || []
                            delegate: Rectangle {
                                width: ListView.view.width
                                height: 42
                                color: index % 2 === 0 ? "#ffffff" : "#f8fafc"
                                border.color: "#e5e7eb"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 10
                                    Label { text: modelData.full_name || (modelData.first_name + " " + modelData.last_name); font.bold: true; Layout.preferredWidth: 210; elide: Text.ElideRight; color: "#172033" }
                                    Label { text: modelData.gender || "-"; Layout.preferredWidth: 64; color: "#475467"; elide: Text.ElideRight }
                                    Label { text: modelData.source_school || "-"; Layout.preferredWidth: 140; color: "#475467"; elide: Text.ElideRight }
                                    Label { text: formatValue(modelData.average_grade); Layout.preferredWidth: 86; color: "#475467"; elide: Text.ElideRight }
                                    Label {
                                        text: modelData.requested_friends ? (modelData.got_friend ? "קיבל/ה חבר" : "חסר: " + modelData.requested_friends) : "ללא בקשה"
                                        color: modelData.got_friend ? "#067647" : "#b54708"
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        footer: RowLayout {
            Button {
                text: "סגירה"
                Layout.fillWidth: true
                onClicked: classDetailsDialog.close()
            }
        }
    }

    Dialog {
        id: bulkClassConstraintDialog
        modal: true
        title: "הגבלת תלמידים לכיתות"
        standardButtons: Dialog.NoButton
        width: Math.min(940, window.width - 48)
        height: Math.min(680, window.height - 72)
        x: Math.round((window.width - width) / 2)
        y: Math.round((window.height - height) / 2)
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Label {
                text: "בחרו תלמידים וכיתות מותרות. אחרי שמירה, כל תלמיד שנבחר יוכל להשתבץ רק בכיתות שסומנו כאן."
                color: "#475467"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: bulkConstraintStudents.length + " תלמידים נבחרו · " + bulkConstraintClasses.length + " כיתות מותרות"
                    font.bold: true
                    color: "#172033"
                    Layout.fillWidth: true
                }
                Button {
                    text: "ניקוי בחירה"
                    onClicked: {
                        bulkConstraintStudents = []
                        bulkConstraintClasses = []
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 14
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 8
                    color: "#f8fafc"
                    border.color: "#dde5ef"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8
                        Label { text: "תלמידים"; font.bold: true; font.pixelSize: 16; color: "#172033" }
                        TextField {
                            placeholderText: "חיפוש שם, קוד או בית ספר"
                            text: bulkConstraintSearch
                            onTextChanged: bulkConstraintSearch = text
                            Layout.fillWidth: true
                        }
                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: filteredStudentsForBulkConstraint()
                            delegate: Rectangle {
                                width: ListView.view.width
                                height: 42
                                color: containsValue(bulkConstraintStudents, modelData.id) ? "#ecfdf3" : (index % 2 === 0 ? "#ffffff" : "#f8fafc")
                                border.color: containsValue(bulkConstraintStudents, modelData.id) ? "#079455" : "#e5e7eb"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 8
                                    CheckBox {
                                        checked: containsValue(bulkConstraintStudents, modelData.id)
                                        Layout.alignment: Qt.AlignVCenter
                                        transform: Translate { y: -3 }
                                        onToggled: bulkConstraintStudents = toggledList(bulkConstraintStudents, modelData.id)
                                    }
                                    Label {
                                        text: (modelData.full_name || (modelData.first_name + " " + modelData.last_name)) + " · " + (modelData.internal_code || "")
                                        Layout.fillWidth: true
                                        color: "#172033"
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        text: modelData.source_school || ""
                                        Layout.preferredWidth: 110
                                        color: "#667085"
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    Layout.preferredWidth: 300
                    Layout.fillHeight: true
                    radius: 8
                    color: "#ffffff"
                    border.color: "#dde5ef"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8
                        Label { text: "כיתות מותרות"; font.bold: true; font.pixelSize: 16; color: "#172033" }
                        Label {
                            text: "סמנו את הכיתות היחידות שבהן מותר לשבץ את התלמידים שנבחרו."
                            color: "#667085"
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: classes
                            CheckBox {
                                text: modelData.name
                                checked: containsValue(bulkConstraintClasses, modelData.name)
                                onToggled: bulkConstraintClasses = toggledList(bulkConstraintClasses, modelData.name)
                                Layout.fillWidth: true
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }
            }
        }
        footer: RowLayout {
            spacing: 10
            Button {
                text: "ביטול"
                Layout.fillWidth: true
                onClicked: bulkClassConstraintDialog.close()
            }
            Button {
                text: "שמירת אילוץ"
                highlighted: true
                enabled: bulkConstraintStudents.length > 0 && bulkConstraintClasses.length > 0
                Layout.fillWidth: true
                onClicked: {
                    bridge.applyAllowedClassesToStudents(bulkConstraintStudents, bulkConstraintClasses)
                    bulkClassConstraintDialog.close()
                }
            }
        }
    }

    header: ToolBar {
        height: 58
        background: Rectangle { color: "#ffffff"; border.color: "#dde5ef" }
        RowLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 14
            Image {
                source: typeof appIconUrl === "undefined" ? "" : appIconUrl
                visible: source.toString().length > 0
                Layout.preferredWidth: 34
                Layout.preferredHeight: 34
                fillMode: Image.PreserveAspectFit
                smooth: true
            }
            Label {
                text: "Mosaicly"
                font.pixelSize: 22
                font.bold: true
                color: "#172033"
            }
            Label {
                text: "By Classify"
                color: "#175cd3"
                font.pixelSize: 12
                font.bold: true
                padding: 7
                background: Rectangle {
                    radius: 6
                    color: "#eef4ff"
                    border.color: "#84caff"
                }
            }
            Label {
                text: "פרויקט פעיל: " + currentProjectName()
                color: "#667085"
                elide: Text.ElideLeft
                Layout.fillWidth: true
            }
            Label {
                text: bridge.status
                color: "#0f766e"
                font.bold: true
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 232
            Layout.fillHeight: true
            color: "#ffffff"
            border.color: "#dde5ef"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                Repeater {
                    model: [
                        "פרויקט", "ייבוא", "מיפוי", "בדיקת נתונים",
                        "כללים", "הרצה", "תוצאות", "אילוצים", "דוחות",
                        "ייצוא", "הגדרות", "עזרה"
                    ]
                    Button {
                        text: modelData
                        Layout.fillWidth: true
                        flat: pageIndex !== index
                        highlighted: pageIndex === index
                        onClicked: pageIndex = index
                    }
                }
                Item { Layout.fillHeight: true }
                Label {
                    text: "הכל נשמר מקומית כברירת מחדל."
                    wrapMode: Text.WordWrap
                    color: "#667085"
                    Layout.fillWidth: true
                }
            }
        }

        StackLayout {
            id: stack
            currentIndex: pageIndex
            Layout.fillHeight: true
            Layout.fillWidth: true

            ProjectPage {}
            ImportPage {}
            MappingPage {}
            ValidationPage {}
            RulesPage {}
            RunPage {}
            TeacherResultsPage {}
            ConflictsPage {}
            ReportsPage {}
            ExportPage {}
            SettingsPage {}
            HelpPage {}
        }
    }

    Rectangle {
        visible: bridge.busy
        z: 100
        anchors.fill: parent
        color: "#66000000"
        MouseArea { anchors.fill: parent }
        Rectangle {
            width: Math.min(600, parent.width - 48)
            height: Math.min(parent.height - 48, bridge.busyProgress > 0 ? 342 : 304)
            anchors.centerIn: parent
            radius: 8
            color: "#ffffff"
            border.color: "#0f766e"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 12
                BusyIndicator {
                    running: bridge.busy
                    Layout.alignment: Qt.AlignHCenter
                    visible: bridge.busyProgress <= 0
                }
                Label {
                    text: bridge.busyText || "מבצע פעולה..."
                    font.pixelSize: 18
                    font.bold: true
                    color: "#172033"
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    ProgressBar {
                        from: 0
                        to: 100
                        value: bridge.busyProgress
                        indeterminate: bridge.busyProgress <= 0
                        Layout.fillWidth: true
                    }
                    Label {
                        text: Math.round(bridge.busyProgress) + "%"
                        visible: bridge.busyProgress > 0
                        font.pixelSize: 20
                        font.bold: true
                        color: "#0f766e"
                        Layout.preferredWidth: 58
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 106
                    radius: 8
                    color: "#f8fafc"
                    border.color: "#dde5ef"
                    Label {
                        anchors.fill: parent
                        anchors.margins: 12
                        text: bridge.busyProgressText || "הפעולה מתבצעת ברקע."
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignRight
                        verticalAlignment: Text.AlignVCenter
                        lineHeight: 1.15
                    }
                }
                Label {
                    text: bridge.busyProgress > 0 ? "האחוזים מחושבים לפי שלבי העבודה בפועל: בדיקת נתונים, בניית סידורים, שיפור ובחירת התוצאה." : "אפשר להמתין כאן. כשהפעולה תסתיים המסך יתעדכן אוטומטית."
                    color: "#667085"
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
            }
        }
    }

    component FlowRibbon: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 76
        radius: 8
        color: "#ffffff"
        border.color: "#dde5ef"
        RowLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 10
            FlowStep { label: "פרויקט"; done: hasProject(); detail: hasProject() ? "פתוח" : "חסר" }
            FlowStep { label: "ייבוא"; done: studentCount > 0; detail: studentCount > 0 ? (studentCount + " תלמידים") : "אין תלמידים" }
            FlowStep { label: "בדיקה"; done: countIssues("critical") === 0 && studentCount > 0; detail: countIssues("critical") + " שגיאות" }
            FlowStep { label: "כיתות"; done: classes.length > 0; detail: classes.length + " כיתות" }
            FlowStep { label: "שיבוץ"; done: hasAssignment(); detail: hasAssignment() ? ("ציון " + dashboard.score.total_score) : "טרם הורץ" }
            FlowStep { label: "AI"; done: aiReviewData.status === "ai_completed" || aiReviewData.status === "local_only"; detail: projectAiAllowed ? "מאושר בפרויקט" : "לא מאושר" }
        }
    }

    component FlowStep: Rectangle {
        property string label: ""
        property string detail: ""
        property bool done: false
        Layout.fillWidth: true
        Layout.preferredHeight: 52
        radius: 8
        color: done ? "#ecfdf3" : "#f8fafc"
        border.color: done ? "#079455" : "#d0d5dd"
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 2
            Label { text: label; font.bold: true; color: done ? "#067647" : "#344054"; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
            Label { text: detail; color: "#667085"; font.pixelSize: 12; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight }
        }
    }

    component AiReviewPanel: Rectangle {
        Layout.fillWidth: true
        implicitHeight: Math.max(112, aiColumn.implicitHeight + 24)
        radius: 8
        color: aiReviewData.used_ai ? "#ecfdf3" : "#fff7ed"
        border.color: aiReviewData.used_ai ? "#079455" : "#f79009"
        ColumnLayout {
            id: aiColumn
            anchors.fill: parent
            anchors.margins: 12
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                Label { text: "AI ופרטיות"; font.bold: true; font.pixelSize: 16; color: "#172033"; Layout.fillWidth: true }
                MiniBadge {
                    textValue: aiSettingsData.enabled ? "AI מופעל" : "AI כבוי"
                    badgeColor: aiSettingsData.enabled ? "#ecfdf3" : "#f2f4f7"
                    textColor: aiSettingsData.enabled ? "#067647" : "#475467"
                }
            }
            Label {
                text: aiReviewStatusText()
                color: "#344054"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                text: "המלצה נבחרת: " + bestAiRecommendationText()
                color: "#475467"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                text: "שמירה על פרטיות: סיכום הנתונים האוטומטי כולל רק ציונים מסכמים, מדדי איכות, ספירות וכיתות אנונימיות. שמות, הערות ושורות מקור לא נשלחים."
                color: "#667085"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: aiSettingsData.providers || []
                    Rectangle {
                        width: Math.max(180, providerLabel.implicitWidth + 22)
                        height: 30
                        radius: 6
                        color: providerBadgeColor(modelData)
                        border.color: Qt.darker(color, 1.06)
                        Label {
                            id: providerLabel
                            anchors.centerIn: parent
                            text: modelData.provider + ": " + providerStatus(modelData)
                            color: providerTextColor(modelData)
                            font.bold: true
                            font.pixelSize: 12
                        }
                    }
                }
            }
        }
    }

    component PageShell: ScrollView {
        id: shell
        contentWidth: availableWidth
        contentHeight: bodyColumn.implicitHeight + 48
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        property string title: ""
        property string subtitle: ""
        default property alias body: bodyColumn.data
        ColumnLayout {
            id: bodyColumn
            x: 24
            y: 24
            width: Math.max(0, shell.availableWidth - 48)
            spacing: 18
            Label {
                text: shell.title
                font.pixelSize: 30
                font.bold: true
                color: "#172033"
                Layout.fillWidth: true
            }
            Label {
                text: shell.subtitle
                color: "#667085"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                visible: text.length > 0
            }
            FlowRibbon {}
        }
    }

    component Panel: Rectangle {
        color: "#ffffff"
        radius: 8
        border.color: "#dde5ef"
        implicitHeight: Math.max(92, panelContent.implicitHeight + 36)
        Layout.fillWidth: true
        Layout.minimumHeight: implicitHeight
        default property alias content: panelContent.data
        ColumnLayout {
            id: panelContent
            anchors.fill: parent
            anchors.margins: 18
            spacing: 12
        }
    }

    component SectionTitle: Label {
        font.pixelSize: 18
        font.bold: true
        color: "#172033"
        Layout.fillWidth: true
    }

    component AppButton: Button {
        id: appButtonRoot
        property color accentColor: "#0f766e"
        property color quietColor: "#ffffff"
        property color dangerColor: "#b42318"
        implicitHeight: 38
        implicitWidth: Math.max(96, appButtonLabel.implicitWidth + 28)
        padding: 10
        font.bold: true
        background: Rectangle {
            radius: 7
            color: !appButtonRoot.enabled
                ? "#f2f4f7"
                : (appButtonRoot.highlighted || appButtonRoot.checked)
                    ? (appButtonRoot.down ? Qt.darker(appButtonRoot.accentColor, 1.12) : (appButtonRoot.hovered ? Qt.lighter(appButtonRoot.accentColor, 1.08) : appButtonRoot.accentColor))
                    : (appButtonRoot.down ? "#e4e7ec" : (appButtonRoot.hovered ? "#f8fafc" : appButtonRoot.quietColor))
            border.color: !appButtonRoot.enabled
                ? "#d0d5dd"
                : (appButtonRoot.highlighted || appButtonRoot.checked)
                    ? Qt.darker(appButtonRoot.accentColor, 1.08)
                    : "#cbd5e1"
            border.width: 1
        }
        contentItem: Label {
            id: appButtonLabel
            text: appButtonRoot.text
            color: !appButtonRoot.enabled
                ? "#98a2b3"
                : (appButtonRoot.highlighted || appButtonRoot.checked)
                    ? "#ffffff"
                    : "#172033"
            font: appButtonRoot.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WordWrap
            maximumLineCount: 3
            lineHeight: 1.08
            elide: Text.ElideRight
        }
    }

    component MetricCard: Rectangle {
        property string title: ""
        property string value: ""
        property string detail: ""
        property color accent: "#0f766e"
        radius: 8
        color: "#ffffff"
        border.color: "#dde5ef"
        Layout.preferredWidth: 190
        Layout.preferredHeight: 98
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 4
            Label { text: title; color: "#667085"; font.pixelSize: 13; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: value; color: accent; font.pixelSize: 26; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: detail; color: "#475467"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
        }
    }

    component ClassMetricTile: Rectangle {
        property string title: ""
        property string value: ""
        property string detail: ""
        property color accent: "#175cd3"
        Layout.fillWidth: true
        Layout.preferredHeight: 96
        radius: 8
        color: "#ffffff"
        border.color: "#dde5ef"
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 4
            Label { text: title; color: "#667085"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: value; color: accent; font.pixelSize: 24; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: detail; color: "#475467"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
        }
    }

    component ClassInfoBlock: Rectangle {
        property string title: ""
        property string body: ""
        Layout.fillWidth: true
        Layout.preferredHeight: 126
        radius: 8
        color: "#ffffff"
        border.color: "#dde5ef"
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 6
            Label { text: title; font.bold: true; font.pixelSize: 15; color: "#172033"; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: body; color: "#475467"; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.fillHeight: true }
        }
    }

    component MiniBadge: Rectangle {
        property string textValue: ""
        property color badgeColor: "#eef4ff"
        property color textColor: "#175cd3"
        radius: 6
        color: badgeColor
        border.color: Qt.darker(badgeColor, 1.05)
        Layout.preferredHeight: 28
        Layout.preferredWidth: Math.max(72, badgeText.implicitWidth + 18)
        Label {
            id: badgeText
            anchors.centerIn: parent
            text: textValue
            color: textColor
            font.bold: true
            font.pixelSize: 12
        }
    }

    component ProjectPage: PageShell {
        title: "פרויקט"
        subtitle: "יצירת פרויקט חדש או פתיחת פרויקט קיים."

        ColumnLayout {
            width: parent.width
            spacing: 18
            anchors.margins: 24

            Panel {
                Layout.preferredHeight: 300
                GridLayout {
                    columns: 2
                    columnSpacing: 12
                    rowSpacing: 10
                    Layout.fillWidth: true
                    Label { text: "שם פרויקט" }
                    TextField { id: projectName; text: "שיבוץ שכבת ז׳"; Layout.fillWidth: true }
                    Label { text: "שכבה" }
                    TextField { id: gradeLevel; text: "ז׳"; Layout.fillWidth: true }
                    Label { text: "שנת לימודים" }
                    TextField { id: schoolYear; text: "תשפ״ז"; Layout.fillWidth: true }
                    Label { text: "מספר כיתות" }
                    SpinBox { id: classCount; from: 1; to: 20; value: 6; Layout.fillWidth: true }
                    Label { text: "שמות כיתות" }
                    TextField { id: classNames; text: "ז׳2, ז׳3, ז׳4, ז׳5, ז׳6, ז׳7"; Layout.fillWidth: true }
                    Label { text: "הערות" }
                    TextField { id: projectNotes; placeholderText: "אופציונלי"; Layout.fillWidth: true }
                }
                Button {
                    text: "יצירת פרויקט"
                    highlighted: true
                    enabled: projectName.text.trim().length > 0 && classNames.text.trim().length > 0
                    Layout.alignment: Qt.AlignLeft
                    onClicked: {
                        bridge.createProject(projectName.text, gradeLevel.text, schoolYear.text, classCount.value, classNames.text, projectNotes.text)
                    }
                }
            }

            Panel {
                Layout.preferredHeight: 300
                Label { text: "פרויקטים אחרונים"; font.bold: true; font.pixelSize: 18 }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: recentProjectsData
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 64
                        color: currentProjectData.id === modelData.id ? "#ecfdf3" : (index % 2 === 0 ? "#ffffff" : "#f8fafc")
                        border.color: currentProjectData.id === modelData.id ? "#079455" : "#e5e7eb"
                        radius: 6
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 10
                            Button {
                                text: "פתח"
                                highlighted: currentProjectData.id === modelData.id
                                Layout.preferredWidth: 70
                                Layout.preferredHeight: 34
                                Layout.alignment: Qt.AlignVCenter
                                onClicked: bridge.openProject(modelData.id)
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.alignment: Qt.AlignVCenter
                                spacing: 2
                                Label { text: modelData.name; font.bold: true; color: "#172033"; Layout.fillWidth: true; elide: Text.ElideRight }
                                Label { text: modelData.grade_level + " · " + modelData.school_year; color: "#667085"; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                            }
                            Button {
                                id: deleteProjectButton
                                text: "מחיקה"
                                Layout.preferredWidth: 88
                                Layout.preferredHeight: 34
                                Layout.alignment: Qt.AlignVCenter
                                background: Rectangle {
                                    radius: 6
                                    color: deleteProjectButton.down ? "#fecdca" : (deleteProjectButton.hovered ? "#fee4e2" : "#fff1f3")
                                    border.color: "#fda29b"
                                }
                                contentItem: Label {
                                    text: deleteProjectButton.text
                                    color: "#b42318"
                                    font.bold: true
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                onClicked: {
                                    pendingDeleteProjectId = modelData.id
                                    pendingDeleteProjectName = modelData.name
                                    deleteProjectDialog.open()
                                }
                            }
                        }
                    }
                }
            }
            Panel {
                visible: hasProject()
                Layout.preferredHeight: 126
                SectionTitle { text: "הרשאת AI לפרויקט" }
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: projectAiAllowed ? "בפרויקט הזה מותר לשלוח נתונים אנונימיים בלבד לשירות AI מוגדר." : "בפרויקט הזה אין הרשאה לשליחה ל־AI חיצוני; כל הסברים ייווצרו מקומית."
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Switch {
                        id: projectAiSwitch
                        text: checked ? "מאושר" : "לא מאושר"
                        checked: projectAiAllowed
                    }
                    AppButton {
                        text: "שמירת הרשאה"
                        highlighted: true
                        onClicked: {
                            bridge.setProjectAllowsExternalAi(projectAiSwitch.checked)
                        }
                    }
                }
            }
            Panel {
                visible: hasProject()
                Layout.preferredHeight: 132
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "כיתות בפרויקט"; Layout.fillWidth: true }
                }
                TextField {
                    id: projectClassesEditor
                    Layout.fillWidth: true
                    text: classes.map(function(c) { return c.name }).join(", ")
                }
                AppButton {
                    text: "שמירת כיתות"
                    highlighted: true
                    enabled: projectClassesEditor.text.trim().length > 0
                    Layout.alignment: Qt.AlignLeft
                    onClicked: {
                        bridge.updateClasses(projectClassesEditor.text)
                    }
                }
            }

            Panel {
                Layout.preferredHeight: 260
                SectionTitle { text: "תיקון ערכים תקניים" }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "המערכת מנרמלת ערכים נפוצים כמו זכר/M/נקבה/F והתנהגות מצוין/בינוני/בעייתי."; color: "#475467"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Button {
                        text: "החלת נרמול מחדש"
                        enabled: hasProject() && studentCount > 0
                        onClicked: {
                            bridge.applyStandardNormalizations()
                        }
                    }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: bridge.normalizationRules()
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 32
                        color: index % 2 === 0 ? "#ffffff" : "#f8fafc"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 6
                            Label { text: modelData.field; Layout.preferredWidth: 110; font.bold: true }
                            Label { text: modelData.source; Layout.fillWidth: true }
                            Label { text: "→ " + modelData.target; Layout.preferredWidth: 160; color: "#067647" }
                        }
                    }
                }
            }
        }
    }

    component ImportPage: PageShell {
        title: "ייבוא קובץ"
        subtitle: "תמיכה ב־CSV וב־XLSX. המערכת מציגה תצוגה מקדימה ומציעה מיפוי בעמוד הבא."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 230
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 112
                    radius: 8
                    color: dropArea.containsDrag ? "#ecfeff" : "#f8fafc"
                    border.color: dropArea.containsDrag ? "#0891b2" : "#cbd5e1"
                    border.width: 1
                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 8
                        Label { text: "גררו לכאן קובץ תלמידים או בחרו ידנית"; font.pixelSize: 18; font.bold: true; color: "#172033" }
                        Label { text: "קבצי CSV ו־XLSX · תצוגה מקדימה של 20 שורות"; color: "#667085" }
                    }
                    DropArea {
                        id: dropArea
                        anchors.fill: parent
                        onDropped: function(drop) {
                            if (drop.hasUrls && drop.urls.length > 0) {
                                importFileUrl = drop.urls[0].toString()
                                bridge.previewFileAsync(importFileUrl, "")
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Button { text: "בחירת קובץ"; highlighted: true; onClicked: importDialog.open() }
                    Label {
                        text: previewData.row_count ? ("זוהו " + previewData.row_count + " תלמידים · " + previewData.headers.length + " עמודות") : "עדיין לא נבחר קובץ"
                        color: "#475467"
                        Layout.fillWidth: true
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: previewData.sheet_names.length > 1
                    Label { text: "גיליון"; Layout.preferredWidth: 90 }
                    ComboBox {
                        id: sheetCombo
                        Layout.fillWidth: true
                        model: previewData.sheet_names
                        currentIndex: Math.max(0, previewData.sheet_names.indexOf(previewData.selected_sheet))
                    }
                    Button {
                        text: "טעינת גיליון"
                        enabled: importFileUrl.length > 0 && sheetCombo.currentText.length > 0
                        onClicked: {
                            if (importFileUrl.length > 0) {
                                bridge.previewFileAsync(importFileUrl, sheetCombo.currentText)
                            }
                        }
                    }
                }
            }

            Panel {
                Layout.preferredHeight: 470
                Label { text: "תצוגה מקדימה"; font.bold: true; font.pixelSize: 18 }
                PreviewTable { rows: previewData.rows; headers: previewData.headers }
            }
        }
    }

    component MappingPage: PageShell {
        title: "מיפוי עמודות"
        subtitle: "אשרו או שנו את ההצעות. חובה שיהיה שם מלא או שם פרטי + שם משפחה."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 620
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        text: "מיפוי אוטומטי"
                        enabled: hasPreview()
                        onClicked: {
                            mappingData = bridge.autoMapping()
                        }
                    }
                    AppButton {
                        text: mappingAiBusy ? "בודק..." : "הצעת AI למיפוי"
                        enabled: hasPreview() && !mappingAiBusy
                        onClicked: {
                            mappingAiBusy = true
                            mappingAiOutput = "מכין הצעת מיפוי. אפשר להמשיך לעבוד במסך בזמן שהבדיקה רצה..."
                            bridge.aiSuggestMappingAsync("mapping", projectAiAllowed)
                        }
                    }
                    AppButton {
                        text: "טעינת תבנית"
                        enabled: hasPreview()
                        onClicked: {
                            mappingData = bridge.loadLatestMappingTemplate()
                        }
                    }
                    AppButton {
                        text: "שמירת תבנית"
                        enabled: hasPreview()
                        onClicked: bridge.saveMappingTemplate("תבנית אחרונה")
                    }
                    AppButton {
                        text: "אישור וטעינת התלמידים לפרויקט"
                        highlighted: true
                        enabled: hasProject() && hasPreview()
                        onClicked: {
                            bridge.saveImportedStudentsAsync()
                            pageIndex = 3
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                RowLayout {
                    Layout.fillWidth: true
                    MiniBadge { textValue: "חובה: שם מלא"; badgeColor: "#ecfdf3"; textColor: "#067647" }
                    MiniBadge { textValue: "אופציונלי: מגדר"; badgeColor: "#eef4ff"; textColor: "#175cd3" }
                    MiniBadge { textValue: "אופציונלי: חברים"; badgeColor: "#fff7ed"; textColor: "#b54708" }
                    Item { Layout.fillWidth: true }
                }
                TextArea {
                    id: mappingAiText
                    Layout.fillWidth: true
                    Layout.preferredHeight: 70
                    readOnly: true
                    wrapMode: TextEdit.WordWrap
                    text: mappingAiOutput
                    visible: mappingAiOutput.length > 0 || mappingAiBusy
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    ColumnLayout {
                        width: parent.width
                        Repeater {
                            model: mappingData
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                Label {
                                    text: modelData.label
                                    Layout.preferredWidth: 180
                                    color: "#172033"
                                }
                                ComboBox {
                                    id: mappingCombo
                                    Layout.fillWidth: true
                                    model: ["לא ממופה"].concat(previewData.headers)
                                    currentIndex: Math.max(0, model.indexOf(modelData.source || "לא ממופה"))
                                    onActivated: {
                                        bridge.setMapping(modelData.field, currentText === "לא ממופה" ? "" : currentText)
                                    }
                                }
                                Label {
                                    text: modelData.help || ""
                                    color: "#667085"
                                    Layout.preferredWidth: 290
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    component ValidationPage: PageShell {
        title: "בדיקת נתונים"
        subtitle: "שגיאות קריטיות כדאי לתקן לפני הרצה. אזהרות אינן חוסמות שיבוץ."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                MetricCard { title: "שגיאות קריטיות"; value: String(countIssues("critical")); detail: "דורשות טיפול"; accent: "#d92d20" }
                MetricCard { title: "אזהרות"; value: String(countIssues("warning")); detail: "לא חוסמות הרצה"; accent: "#b54708" }
                MetricCard { title: "מידע"; value: String(countIssues("info")); detail: "בדיקות מומלצות"; accent: "#175cd3" }
                Item { Layout.fillWidth: true }
            }
            Panel {
                id: validationPanel
                Layout.preferredHeight: 620
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "נמצאו " + validationIssuesData.length + " נושאים לבדיקה"
                        font.bold: true
                        font.pixelSize: 18
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        text: "ייצוא שגיאות לאקסל"
                        enabled: hasProject() && validationIssuesData.length > 0
                        onClicked: validationExportDialog.open()
                    }
                    Button {
                        text: "רענון"
                        onClicked: validationIssuesData = bridge.validationIssues()
                    }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: validationIssuesData
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: Math.max(54, issueText.implicitHeight + 22)
                        radius: 6
                        color: severityColor(modelData.severity, 0.10)
                        border.color: severityColor(modelData.severity, 1.0)
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 10
                            Label {
                                text: modelData.severity
                                color: severityColor(modelData.severity, 1.0)
                                font.bold: true
                                Layout.preferredWidth: 90
                            }
                            Label {
                                id: issueText
                                text: modelData.message
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                                color: "#172033"
                            }
                        }
                    }
                }
            }
        }
    }

    component ClassesPage: PageShell {
        title: "כיתות"
        subtitle: "עדכון שמות הכיתות. שינוי כיתות מוחק שיוך כיתה קיים ודורש הרצה מחדש."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 400
                Label { text: "שמות כיתות"; font.bold: true; font.pixelSize: 18 }
                TextArea {
                    id: classesEditor
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    text: classes.map(function(c) { return c.name }).join(", ")
                    wrapMode: TextEdit.WordWrap
                }
                Button {
                    text: "שמירת כיתות"
                    highlighted: true
                    enabled: hasProject() && classesEditor.text.trim().length > 0
                    onClicked: {
                        bridge.updateClasses(classesEditor.text)
                    }
                }
            }
        }
    }

    component RulesPage: PageShell {
        title: "כללי שיבוץ"
        subtitle: "כל כלל אופציונלי, מלבד איזון גודל כיתות שמומלץ להשאיר פעיל."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                id: rulesPanel
                Layout.preferredHeight: developerUnlocked ? 1120 : 760
                property var settings: bridge.ruleSettings()
                SectionTitle { text: "מגבלות בסיסיות" }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 12
                    rowSpacing: 8
                    Label { text: "מקסימום תלמידים בכיתה" }
                    SpinBox { id: maxClassSizeBox; from: 1; to: 60; value: rulesPanel.settings.max_students_per_class || 40; Layout.fillWidth: true }
                    Label { text: "מקסימום מכל מגדר בכיתה" }
                    SpinBox { id: maxGenderBox; from: 1; to: 40; value: rulesPanel.settings.max_students_per_gender || 20; Layout.fillWidth: true }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Button {
                        text: developerUnlocked ? "הגדרות מפתח פתוחות" : "פתיחת הגדרות מפתח"
                        highlighted: developerUnlocked
                        onClicked: {
                            if (!developerUnlocked) developerPasswordDialog.open()
                        }
                    }
                    Button {
                        visible: developerUnlocked
                        text: "נעילת הגדרות מפתח"
                        onClicked: developerUnlocked = false
                    }
                    Item { Layout.fillWidth: true }
                }
                SectionTitle { text: "הגדרות מתקדמות של השיבוץ"; visible: developerUnlocked }
                GridLayout {
                    visible: developerUnlocked
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 12
                    rowSpacing: 8
                    Label { text: "שיטת חישוב" }
                    ComboBox {
                        id: optimizerBackendBox
                        model: ["אוטומטי", "מקומי", "מדויק"]
                        currentIndex: backendIndex(rulesPanel.settings.optimizer_backend || "auto")
                        Layout.fillWidth: true
                    }
                    Label { text: "זמן חיפוש בשניות" }
                    SpinBox { id: optimizerTimeBox; from: 1; to: 30; value: rulesPanel.settings.optimizer_time_limit_seconds || 8; Layout.fillWidth: true }
                    Label { text: "כמה ניסיונות התחלה" }
                    SpinBox { id: restartBox; from: 1; to: 10; value: rulesPanel.settings.search_restarts || 6; Layout.fillWidth: true }
                    Label { text: "עצירה כשמגיעים לציון" }
                    SpinBox { id: stopScoreBox; from: 70; to: 99; value: rulesPanel.settings.stop_when_score_at_least || 92; Layout.fillWidth: true }
                    Label { text: "פער ציונים שמותר" }
                    SpinBox { id: gradeToleranceBox; from: 0; to: 15; value: Math.round(rulesPanel.settings.grade_tolerance || 4); Layout.fillWidth: true }
                    Label { text: "פער מגדר שמותר באחוזים" }
                    SpinBox { id: genderToleranceBox; from: 0; to: 40; value: Math.round(rulesPanel.settings.gender_tolerance || 10); Layout.fillWidth: true }
                    Label { text: "פער התנהגות שמותר" }
                    SpinBox { id: behaviorToleranceBox; from: 0; to: 100; value: Math.round(Number(rulesPanel.settings.behavior_tolerance || 0.35) * 100); Layout.fillWidth: true }
                }
                Rectangle {
                    visible: developerUnlocked
                    Layout.fillWidth: true
                    Layout.preferredHeight: 74
                    radius: 8
                    color: "#f8fafc"
                    border.color: "#dde5ef"
                    Label {
                        anchors.fill: parent
                        anchors.margins: 10
                        text: "אלה הגדרות למתקדמים. ברירת המחדל מתאימה לרוב המקרים: המערכת מנסה כמה סידורים ראשוניים, בוחרת את הטוב ביותר, ועוצרת מוקדם אם כבר התקבל שיבוץ איכותי. \"פער שמותר\" אומר כמה הבדל קטן בין כיתות עדיין נחשב תקין."
                        color: "#475467"
                        wrapMode: Text.WordWrap
                    }
                }
                SectionTitle { text: "עדיפויות איזון"; visible: developerUnlocked }
                GridLayout {
                    visible: developerUnlocked
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 20
                    rowSpacing: 6
                    RuleWeight { id: classSizeWeight; title: "גודל כיתה"; value: Number(rulesPanel.settings.class_size_weight || 1.2); help: "כמה חשוב שכל הכיתות יהיו דומות במספר התלמידים. ערך גבוה נותן לזה עדיפות חזקה יותר." }
                    RuleWeight { id: genderWeight; title: "מגדר"; value: Number(rulesPanel.settings.gender_weight || 1.0); help: "כמה חשוב לצמצם פערי בנים/בנות בין כיתות. פער קטן שמוגדר למעלה עדיין נחשב תקין." }
                    RuleWeight { id: gradeWeight; title: "ממוצע כללי"; value: Number(rulesPanel.settings.grade_weight || 1.1); help: "כמה חשוב שממוצע הציונים הכללי יהיה דומה בין הכיתות." }
                    RuleWeight { id: subjectWeight; title: "מקצועות"; value: Number(rulesPanel.settings.subject_weight || 0.6); help: "איזון נפרד למתמטיקה, אנגלית ועברית. משקל גבוה מונע כיתה חזקה מדי במקצוע אחד." }
                    RuleWeight { id: behaviorWeight; title: "התנהגות"; value: Number(rulesPanel.settings.behavior_weight || 1.0); help: "כמה חשוב לפזר רמות התנהגות ואתגרי התנהגות בין הכיתות." }
                    RuleWeight { id: dominanceWeight; title: "דומיננטיות"; value: Number(rulesPanel.settings.dominance_weight || 0.8); help: "כמה חזק לפזר תלמידים דומיננטיים או מאתגרים כדי שלא יתרכזו בכיתה אחת." }
                    RuleWeight { id: friendshipWeight; title: "חברים"; value: Number(rulesPanel.settings.friendship_weight || 2.2); help: "כמה חשוב לספק לפחות חבר מבוקש. משקל גבוה עשוי לפגוע מעט באיזונים אחרים." }
                    RuleWeight { id: sourceWeight; title: "בתי ספר מקור"; value: Number(rulesPanel.settings.source_school_weight || 1.1); help: "כמה חשוב לפזר בתי ספר מקור באופן שווה בין הכיתות, תוך ניסיון לא להשאיר תלמיד יחיד מבודד חברתית." }
                }
                RuleSwitch { id: aiAssistRule; visible: developerUnlocked; title: "ניסיון שיפור אוטומטי נוסף"; checked: rulesPanel.settings.ai_assisted_assignment; help: "זו בדיקה מקומית במחשב, לא AI חיצוני. המערכת מזהה את הנושא שהכי פוגע באיכות, מנסה לשפר אותו, ומחליפה את השיבוץ רק אם התקבלה תוצאה טובה יותר." }
                RuleSwitch { id: sizeRule; title: "איזון מספר תלמידים"; checked: true; enabled: false }
                RuleSwitch { id: genderRule; title: "איזון מגדר"; checked: rulesPanel.settings.balance_gender; help: "מנסה שהיחס בין בנים לבנות יהיה דומה בין הכיתות." }
                RuleSwitch { id: gradeRule; title: "איזון ציונים"; checked: rulesPanel.settings.balance_grades; help: "מפעיל איזון ממוצע כללי וגם איזון מקצועות אם קיימים ציוני מקצוע." }
                RuleSwitch { id: behaviorRule; title: "איזון התנהגות"; checked: rulesPanel.settings.balance_behavior; help: "מנסה שלא לרכז תלמידים עם אותה רמת התנהגות בכיתה אחת." }
                RuleSwitch { id: dominantRule; title: "פיזור תלמידים דומיננטיים"; checked: rulesPanel.settings.spread_dominant_students; help: "משתמש בשדה דומיננטיות/אתגר כדי לפזר עומס כיתתי." }
                RuleSwitch { id: friendRule; title: "בחירת חברים"; checked: rulesPanel.settings.friendship; help: "מנסה לתת לכל תלמיד לפחות חבר מבוקש, כל עוד זה לא שובר כללים מחייבים." }
                RuleSwitch { id: friendshipRequiredRule; title: "חייב לפחות חבר אחד"; checked: rulesPanel.settings.friendship_required !== false; enabled: friendRule.checked; help: "כאשר פעיל, כל תלמיד עם בקשת חברים חייב לקבל לפחות חבר מבוקש אחד. אם זה בלתי אפשרי מול אילוצים קשיחים, יוצג השיבוץ הכי טוב שנמצא עם פירוט ההפרות." }
                RuleSwitch { id: friendshipFirstRule; title: "חברים קודם"; checked: rulesPanel.settings.friendship_first || false; enabled: friendRule.checked; help: "כשפעיל, המערכת מדרגת קודם שיבוץ שבו כמה שיותר תלמידים קיבלו לפחות חבר אחד, ורק אחר כך מאזנת מגדר, ציונים, כיתה ושאר המדדים." }
                RuleSwitch { id: friendPriorityRule; title: "עדיפות לפי סדר חברים"; checked: rulesPanel.settings.friendship_priority_order; help: "כשפעיל: חבר 1 חשוב יותר מחבר 2, וחבר 2 חשוב יותר מחבר 3. כשכבוי: שלושת החברים שווים, והמערכת מנסה לתת כמה שיותר חברים." }
                RuleSwitch { id: sourceRule; title: "פיזור בית ספר מקור"; checked: rulesPanel.settings.spread_source_school; help: "מונע ריכוז גדול מדי של אותו בית ספר מקור בכיתה אחת." }
                RuleSwitch { id: isolationRule; title: "מניעת בדידות חברתית"; checked: rulesPanel.settings.avoid_social_isolation; help: "מנסה למנוע מצב שבו תלמיד נשאר יחיד מבית הספר המקור שלו כשיש עוד תלמידים מאותו מקור." }
                RuleSwitch { id: capacityRule; title: "מגבלת גודל כיתה מחייבת"; checked: true; enabled: false; help: "מקסימום תלמידים בכיתה ומקסימום תלמידים מכל מגדר הם חוקי ברזל כשמוגדרים מספרים." }
                RuleSwitch { id: aiAutoRule; title: "ניתוח AI/מקומי אוטומטי לשיבוץ חלש"; checked: rulesPanel.settings.ai_auto_review; help: "אחרי השיבוץ: אם הציון נמוך מהסף, נשלח דוח אנונימי ונבדקות הצעות פעולה. AI חיצוני לא מקבל שמות ולא ממציא כיתות." }
                RowLayout {
                    visible: developerUnlocked
                    Label { text: "מספר איטרציות"; Layout.preferredWidth: 180 }
                    SpinBox { id: iterationBox; from: 80; to: 2000; value: rulesPanel.settings.max_iterations || 220; stepSize: 20 }
                }
                RowLayout {
                    visible: developerUnlocked
                    Label { text: "סף AI"; Layout.preferredWidth: 180 }
                    SpinBox { id: aiThresholdBox; from: 40; to: 95; value: rulesPanel.settings.ai_review_threshold || 78; stepSize: 1 }
                    Label { text: "AI נבדק אוטומטית רק אם הציון מתחת לסף"; color: "#667085"; Layout.fillWidth: true }
                }
                Button {
                    text: "שמירת כללים"
                    highlighted: true
                    enabled: hasProject()
                    onClicked: {
                        bridge.saveRuleSettings({
                            "balance_class_size": true,
                            "balance_gender": genderRule.checked,
                            "balance_grades": gradeRule.checked,
                            "balance_behavior": behaviorRule.checked,
                            "spread_dominant_students": dominantRule.checked,
                            "friendship": friendRule.checked,
                            "friendship_required": friendshipRequiredRule.checked,
                            "friendship_first": friendshipFirstRule.checked,
                            "friendship_priority_order": friendPriorityRule.checked,
                            "spread_source_school": sourceRule.checked,
                            "avoid_social_isolation": isolationRule.checked,
                            "hard_class_capacity": true,
                            "max_students_per_class": maxClassSizeBox.value,
                            "max_students_per_gender": maxGenderBox.value,
                            "class_size_weight": classSizeWeight.value,
                            "gender_weight": genderWeight.value,
                            "grade_weight": gradeWeight.value,
                            "subject_weight": subjectWeight.value,
                            "behavior_weight": behaviorWeight.value,
                            "dominance_weight": dominanceWeight.value,
                            "friendship_weight": friendshipWeight.value,
                            "source_school_weight": sourceWeight.value,
                            "grade_tolerance": gradeToleranceBox.value,
                            "gender_tolerance": genderToleranceBox.value,
                            "behavior_tolerance": behaviorToleranceBox.value / 100,
                            "max_iterations": iterationBox.value,
                            "search_restarts": restartBox.value,
                            "stop_when_score_at_least": stopScoreBox.value,
                            "optimizer_backend": backendValue(optimizerBackendBox.currentText),
                            "optimizer_time_limit_seconds": optimizerTimeBox.value,
                            "ai_assisted_assignment": aiAssistRule.checked,
                            "ai_auto_review": aiAutoRule.checked,
                            "ai_review_threshold": aiThresholdBox.value
                        })
                    }
                }
                SectionTitle { text: "המלצת AI לכללים"; visible: developerUnlocked }
                RowLayout {
                    visible: developerUnlocked
                    Layout.fillWidth: true
                    Button {
                        text: "קבלת המלצה"
                        enabled: hasProject()
                        onClicked: bridge.requestAiRuleRecommendationsAsync()
                    }
                    Button {
                        text: "החלת המלצה"
                        highlighted: true
                        enabled: aiRuleData && aiRuleData.recommendation && aiRuleData.recommendation.settings
                        onClicked: {
                            var result = bridge.applyAiRuleRecommendation()
                            settingsAiOutput = result.message || ""
                            rulesPanel.settings = bridge.ruleSettings()
                        }
                    }
                    MiniBadge {
                        textValue: aiRuleData && aiRuleData.used_ai ? "AI" : (aiRuleData.status === "running" ? "בודק" : "מקומי")
                        badgeColor: aiRuleData && aiRuleData.used_ai ? "#ecfdf3" : (aiRuleData.status === "running" ? "#eef4ff" : "#fff7ed")
                        textColor: aiRuleData && aiRuleData.used_ai ? "#067647" : (aiRuleData.status === "running" ? "#175cd3" : "#b54708")
                    }
                    Item { Layout.fillWidth: true }
                }
                Label {
                    visible: developerUnlocked
                    text: aiRuleData.status === "running"
                          ? "AI בודק את סיכום הנתונים האנונימי מול שלושת הספקים המוגדרים."
                          : ((aiRuleData.recommendation && aiRuleData.recommendation.summary_he)
                             ? (aiRuleData.recommendation.summary_he + " " + (aiRuleData.recommendation.reason_he || ""))
                             : "אפשר לבקש המלצת כללים מותאמת אישית לפי מצב הנתונים והשיבוץ.")
                    color: "#475467"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    visible: developerUnlocked && aiRuleData.recommendation && aiRuleData.recommendation.settings
                    text: {
                        var s = aiRuleData.recommendation && aiRuleData.recommendation.settings ? aiRuleData.recommendation.settings : {}
                        return "המלצה: חברים קודם " + (s.friendship_first ? "פעיל" : "כבוי")
                            + " · משקל חברים " + Number(s.friendship_weight || 0).toFixed(1)
                            + " · ניסיונות " + (s.search_restarts || "-")
                            + " · איטרציות " + (s.max_iterations || "-")
                    }
                    color: "#172033"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    visible: developerUnlocked
                    Layout.fillWidth: true
                    Button {
                        text: "הגבלת תלמידים לכיתות"
                        enabled: hasProject() && studentCount > 0 && classes.length > 0
                        onClicked: {
                            bulkConstraintStudents = []
                            bulkConstraintClasses = []
                            bulkConstraintSearch = ""
                            bulkClassConstraintDialog.open()
                        }
                    }
                    Button {
                        text: "שמירת תבנית כללים"
                        enabled: hasProject()
                        onClicked: bridge.saveRuleTemplate("תבנית כללים אחרונה")
                    }
                    Button {
                        text: "טעינת תבנית כללים"
                        enabled: hasProject()
                        onClicked: {
                            rulesPanel.settings = bridge.loadLatestRuleTemplate()
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
            }
        }
    }

    component RunPage: PageShell {
        title: "הרצת שיבוץ"
        subtitle: "המערכת מנסה כמה סידורי כיתות אפשריים, שומרת את הסידור הטוב ביותר, ומציגה הסבר אחרי ההרצה."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 650
                GridLayout {
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 14
                    rowSpacing: 8
                    Label { visible: developerUnlocked; text: "כמה סידורים לנסות"; font.bold: true }
                    SpinBox { id: candidateCountBox; visible: developerUnlocked; from: 1; to: 24; value: 5; Layout.fillWidth: true }
                    Label { text: "ספקי AI לבדיקה"; font.bold: true }
                    SpinBox { id: aiReviewerCountBox; from: 3; to: 3; value: 3; enabled: false; Layout.fillWidth: true }
                    Label { text: "לבקש הסבר גם אם השיבוץ טוב"; font.bold: true }
                    Switch { id: forceAiReviewSwitch; text: checked ? "כן" : "לא"; checked: false; Layout.fillWidth: true }
                    Label { text: "שימוש ב-AI בפרויקט"; font.bold: true }
                    Label { text: projectAiAllowed ? "מאושרת" : "לא מאושרת - תתבצע סקירה מקומית"; color: projectAiAllowed ? "#067647" : "#b54708"; Layout.fillWidth: true }
                }
                Label {
                    text: "כל ניסיון הוא סידור אפשרי אחר של התלמידים בכיתות. בסיום נשמר הסידור המקומי הטוב ביותר. בהרצת MAX נשמרות חמש התוצאות המובילות, ואם יש הרשאה נשלחות גם פעולות העברה/החלפה אנונימיות ל-AI כדי לבחור הצעות שיפור נוספות."
                    color: "#475467"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                AppButton {
                    text: "התחלת שיבוץ"
                    highlighted: true
                    enabled: hasProject() && studentCount > 0 && classes.length > 0
                    Layout.preferredHeight: 46
                    onClicked: {
                        bridge.runAssignmentAsync(candidateCountBox.value, forceAiReviewSwitch.checked, aiReviewerCountBox.value)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        text: "הרצת MAX"
                        highlighted: true
                        enabled: hasProject() && studentCount > 0 && classes.length > 0
                        Layout.preferredHeight: 46
                        Layout.preferredWidth: 180
                        onClicked: bridge.runMaxAssignmentAsync()
                    }
                    Button {
                        text: developerUnlocked ? "הגדרות מפתח פתוחות" : "פתיחת הגדרות מפתח"
                        highlighted: developerUnlocked
                        onClicked: {
                            if (!developerUnlocked) developerPasswordDialog.open()
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 280
                    radius: 8
                    color: "#f8fafc"
                    border.color: "#dde5ef"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "בדיקת חברים"
                                font.pixelSize: 16
                                font.bold: true
                                color: "#172033"
                                Layout.fillWidth: true
                            }
                            AppButton {
                                text: "בדיקת חברים"
                                enabled: hasProject() && studentCount > 0 && classes.length > 0 && friendshipDiagnosticData.status !== "running"
                                Layout.preferredWidth: 150
                                onClicked: runFriendshipDiagnostic({
                                    "class_size": friendDiagClassSize.checked,
                                    "gender": friendDiagGender.checked,
                                    "class_constraints": friendDiagClassConstraints.checked,
                                    "together": friendDiagTogether.checked,
                                    "separation": friendDiagSeparation.checked
                                })
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 12
                            CheckBox { id: friendDiagClassSize; text: "גודל כיתות"; checked: true }
                            CheckBox { id: friendDiagGender; text: "מגדר"; checked: true }
                            CheckBox { id: friendDiagClassConstraints; text: "כיתות/נעילות"; checked: true }
                            CheckBox { id: friendDiagTogether; text: "חייב להיות עם"; checked: true }
                            CheckBox { id: friendDiagSeparation; text: "לא לשבץ עם"; checked: true }
                        }
                        Label {
                            text: friendshipDiagnosticStatusText()
                            color: friendshipDiagnosticData.status === "done" && friendshipDiagnosticResult().legal_full_friend_coverage ? "#067647" : (friendshipDiagnosticData.status === "failed" ? "#b42318" : "#172033")
                            font.bold: true
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            text: friendshipDiagnosticSummaryText()
                            color: "#475467"
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            maximumLineCount: 10
                            elide: Text.ElideRight
                        }
                    }
                }
                Label {
                    text: dashboard.has_assignment ? ("ציון נוכחי: " + dashboard.score.total_score + " · " + dashboard.score.summary) : (studentCount > 0 ? "עדיין אין שיבוץ פעיל." : "ייבאו תלמידים לפני הרצת שיבוץ.")
                    wrapMode: Text.WordWrap
                    color: "#172033"
                    Layout.fillWidth: true
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 86
                    radius: 8
                    color: "#eef4ff"
                    border.color: "#84caff"
                    Label {
                        anchors.fill: parent
                        anchors.margins: 12
                        text: "הבחירה הסופית נעשית לפי ציון השיבוץ המקומי. בהרצת MAX נשמרות גם התוצאות הבאות ברשימה כדי שתוכלו להשוות, ואם יש הרשאת פרויקט AI מקבל מזהים אנונימיים ופעולות מועמדות בלבד כדי להציע שיפורים נוספים."
                        color: "#1849a9"
                        wrapMode: Text.WordWrap
                    }
                }
                AiReviewPanel {}
                ListView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 120
                    model: dashboard.has_assignment ? dashboard.score.hard_violations : []
                    delegate: Label {
                        width: ListView.view.width
                        text: "• " + modelData
                        color: "#b42318"
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }

    component ResultsPageOld: PageShell {
        title: "תוצאות"
        subtitle: "תצוגת כיתות, כרטיס תלמיד, תיקונים ידניים והצעות החלפה."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 16
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                MetricCard {
                    title: "ציון כללי"
                    value: dashboard.has_assignment ? String(dashboard.score.total_score) : "-"
                    detail: "מתוך 100"
                    accent: dashboard.has_assignment && dashboard.score.total_score >= 80 ? "#067647" : "#b54708"
                }
                MetricCard { title: "גודל כיתות"; value: String(dashboardPenalty("class_size")); detail: "פער נמוך עדיף"; accent: "#0f766e" }
                MetricCard { title: "חברים"; value: String(dashboardPenalty("friendship")); detail: "בקשות שלא מולאו"; accent: "#7e22ce" }
                MetricCard { title: "כללים מחייבים"; value: String(dashboardPenalty("hard_constraints")); detail: "צריך להיות 0"; accent: "#d92d20" }
                Item { Layout.fillWidth: true }
            }
            AiReviewPanel {}
            Panel {
                Layout.preferredHeight: 188
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: dashboard.has_assignment ? ("ציון: " + dashboard.score.total_score) : "אין שיבוץ פעיל"
                        font.pixelSize: 22
                        font.bold: true
                        color: dashboard.has_assignment && dashboard.score.total_score >= 80 ? "#067647" : "#b54708"
                    }
                    Label {
                        text: dashboard.has_assignment ? dashboard.score.summary : "הריצו שיבוץ כדי לראות תוצאות."
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    AppButton { text: "בטל פעולה"; enabled: hasAssignment(); onClicked: bridge.undo() }
                    AppButton { text: "שחזר פעולה"; enabled: hasAssignment(); onClicked: bridge.redo() }
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 104
                    RowLayout {
                        AppButton {
                            text: "כל הכיתות\n" + (dashboard.has_assignment ? dashboard.rows.length + " תלמידים" : "אין שיבוץ")
                            checkable: true
                            checked: selectedClassId === 0
                            onClicked: chooseClass(0)
                            Layout.preferredWidth: 170
                            Layout.preferredHeight: 86
                        }
                        Repeater {
                            model: dashboard.has_assignment ? dashboard.score.class_stats : []
                            Button {
                                id: classDropButton
                                text: modelData.name + "\n" + modelData.size + " תלמידים · בנים/בנות " + modelData.boys + "/" + modelData.girls + "\nציון " + formatValue(modelData.quality_score) + " · ממוצע " + formatValue(modelData.avg_grade)
                                checkable: true
                                checked: selectedClassId === modelData.class_id
                                onClicked: chooseClass(modelData.class_id)
                                Layout.preferredWidth: 220
                                Layout.preferredHeight: 86
                                DropArea {
                                    anchors.fill: parent
                                    enabled: hasAssignment()
                                    onDropped: function(drop) {
                                        var studentId = drop.getDataAsString("studentId")
                                        if (studentId && studentId.length > 0) {
                                            bridge.moveStudent(parseInt(studentId), modelData.class_id, false)
                                            drop.acceptProposedAction()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Panel {
                SectionTitle { text: "ניתוח כיתתי עמוק" }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 188
                    RowLayout {
                        Repeater {
                            model: dashboard.has_assignment ? dashboard.score.class_stats : []
                            Rectangle {
                                Layout.preferredWidth: 260
                                Layout.preferredHeight: 192
                                radius: 8
                                color: "#f8fafc"
                                border.color: selectedClassId === modelData.class_id ? "#0f766e" : "#dde5ef"
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 4
                                    Label { text: modelData.name; font.bold: true; font.pixelSize: 16; color: "#172033"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "ציון כיתה " + formatValue(modelData.quality_score) + " · " + (modelData.quality_summary || ""); color: modelData.quality_score >= 85 ? "#067647" : (modelData.quality_score >= 70 ? "#b54708" : "#d92d20"); Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "תלמידים: " + modelData.size + " · בנים/בנות: " + modelData.boys + "/" + modelData.girls; color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "ממוצע כללי: " + formatValue(modelData.avg_grade) + " · מת׳: " + formatValue(modelData.math_avg) + " · אנ׳: " + formatValue(modelData.english_avg) + " · עב׳: " + formatValue(modelData.hebrew_avg); color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "התנהגות: " + counterText(modelData.behavior_counts); color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "בתי ספר: " + counterText(modelData.schools); color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "חברים: " + modelData.friends_satisfied + " קיבלו · " + modelData.friends_missing + " חסרים"; color: modelData.friends_missing > 0 ? "#b54708" : "#067647"; Layout.fillWidth: true; elide: Text.ElideRight }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: chooseClass(modelData.class_id)
                                }
                            }
                        }
                    }
                }
            }

            Panel {
                visible: selectedClassId !== 0 && selectedClassStats && selectedClassStats.name
                Layout.preferredHeight: 210
                SectionTitle { text: "נתוני כיתה: " + (selectedClassStats.name || "") }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 18
                    rowSpacing: 8
                    Label { text: "ציון כיתה"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.quality_score) + " · " + (selectedClassStats.quality_summary || ""); color: selectedClassStats.quality_score >= 85 ? "#067647" : (selectedClassStats.quality_score >= 70 ? "#b54708" : "#d92d20"); Layout.fillWidth: true; elide: Text.ElideRight }
                    Label { text: "תלמידים"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.size); Layout.fillWidth: true }
                    Label { text: "מגדר"; font.bold: true }
                    Label { text: counterText(selectedClassStats.gender_counts); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "ממוצע כללי"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.avg_grade); Layout.fillWidth: true }
                    Label { text: "מתמטיקה / אנגלית / עברית"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.math_avg) + " / " + formatValue(selectedClassStats.english_avg) + " / " + formatValue(selectedClassStats.hebrew_avg); Layout.fillWidth: true }
                    Label { text: "התנהגות"; font.bold: true }
                    Label { text: counterText(selectedClassStats.behavior_counts); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "בתי ספר מקור"; font.bold: true }
                    Label { text: counterText(selectedClassStats.schools); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "חברים"; font.bold: true }
                    Label { text: selectedClassStats.friends_satisfied + " קיבלו / " + selectedClassStats.friends_missing + " חסרים"; Layout.fillWidth: true }
                    Label { text: "דומיננטיות"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.dominance_total); Layout.fillWidth: true }
                }
            }

            Panel {
                Layout.preferredHeight: 112
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "גרסאות שיבוץ"; Layout.fillWidth: false; Layout.preferredWidth: 150 }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        RowLayout {
                            Repeater {
                                model: dashboard.has_assignment ? dashboard.versions : []
                                Button {
                                    text: modelData.name + "\nציון " + modelData.score_total
                                    checkable: true
                                    checked: modelData.is_active
                                    Layout.preferredWidth: 180
                                    Layout.preferredHeight: 62
                                    onClicked: {
                                        bridge.selectAssignmentVersion(modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                Panel {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 650
                    Label {
                        text: hasAssignment() ? ("תלמידים בכיתה " + selectedClassName() + " (" + visibleStudents().length + ")") : "אין עדיין תלמידים להצגה"
                        font.bold: true
                        font.pixelSize: 18
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField {
                            placeholderText: "חיפוש לפי שם או קוד"
                            text: studentSearchText
                            onTextChanged: studentSearchText = text
                            Layout.fillWidth: true
                        }
                        ComboBox {
                            model: ["הכל", "נעולים", "שונו ידנית", "בנים", "בנות", "ללא חבר"]
                            currentIndex: Math.max(0, model.indexOf(studentFilterMode))
                            onActivated: studentFilterMode = currentText
                            Layout.preferredWidth: 150
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        CheckBox { text: "כיתה"; checked: showClassColumn; onToggled: showClassColumn = checked }
                        CheckBox { text: "מגדר"; checked: showGenderColumn; onToggled: showGenderColumn = checked }
                        CheckBox { text: "בית ספר"; checked: showSchoolColumn; onToggled: showSchoolColumn = checked }
                        CheckBox { text: "ממוצע"; checked: showGradeColumn; onToggled: showGradeColumn = checked }
                        CheckBox { text: "מתמטיקה"; checked: showMathColumn; onToggled: showMathColumn = checked }
                        CheckBox { text: "אנגלית"; checked: showEnglishColumn; onToggled: showEnglishColumn = checked }
                        CheckBox { text: "עברית"; checked: showHebrewColumn; onToggled: showHebrewColumn = checked }
                        CheckBox { text: "התנהגות"; checked: showBehaviorColumn; onToggled: showBehaviorColumn = checked }
                        CheckBox { text: "חברים"; checked: showFriendsColumn; onToggled: showFriendsColumn = checked }
                        CheckBox { text: "מי ביקש"; checked: showRequestedByColumn; onToggled: showRequestedByColumn = checked }
                        CheckBox { text: "הערות"; checked: showNotesColumn; onToggled: showNotesColumn = checked }
                        CheckBox { text: "אילוצים"; checked: showConstraintsColumn; onToggled: showConstraintsColumn = checked }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 34
                        color: "#f8fafc"
                        border.color: "#e5e7eb"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Label { text: "קוד"; font.bold: true; Layout.preferredWidth: 70 }
                            Label { text: "כיתה"; font.bold: true; visible: showClassColumn; Layout.preferredWidth: 80 }
                            Label { text: "שם"; font.bold: true; Layout.fillWidth: true }
                            Label { text: "מגדר"; font.bold: true; visible: showGenderColumn; Layout.preferredWidth: 55 }
                            Label { text: "בית ספר"; font.bold: true; visible: showSchoolColumn; Layout.preferredWidth: 90 }
                            Label { text: "ממוצע"; font.bold: true; visible: showGradeColumn; Layout.preferredWidth: 65 }
                            Label { text: "מת׳"; font.bold: true; visible: showMathColumn; Layout.preferredWidth: 55 }
                            Label { text: "אנ׳"; font.bold: true; visible: showEnglishColumn; Layout.preferredWidth: 55 }
                            Label { text: "עב׳"; font.bold: true; visible: showHebrewColumn; Layout.preferredWidth: 55 }
                            Label { text: "התנהגות"; font.bold: true; visible: showBehaviorColumn; Layout.preferredWidth: 80 }
                            Label { text: "חברים"; font.bold: true; visible: showFriendsColumn; Layout.preferredWidth: 120 }
                            Label { text: "מי ביקש"; font.bold: true; visible: showRequestedByColumn; Layout.preferredWidth: 120 }
                            Label { text: "הערות"; font.bold: true; visible: showNotesColumn; Layout.preferredWidth: 140 }
                            Label { text: "אילוצים"; font.bold: true; visible: showConstraintsColumn; Layout.preferredWidth: 140 }
                            Label { text: "העברה"; font.bold: true; Layout.preferredWidth: 210 }
                        }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 420
                        clip: true
                        model: visibleStudents()
                        delegate: Rectangle {
                            id: studentRow
                            width: ListView.view.width
                            height: 52
                            radius: 6
                            color: selectedStudentId === modelData.student_id ? "#e0f2fe" : "#ffffff"
                            border.color: modelData.locked_manually ? "#2563eb" : (modelData.changed_manually ? "#7e22ce" : (!modelData.got_friend ? "#f79009" : "#e5e7eb"))
                            Drag.active: dragArea.drag.active
                            Drag.mimeData: { "studentId": String(modelData.student_id) }
                            Drag.supportedActions: Qt.MoveAction
                            Drag.hotSpot.x: width / 2
                            Drag.hotSpot.y: height / 2
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                Label { text: modelData.internal_code; color: "#667085"; Layout.preferredWidth: 70 }
                                Label { text: modelData.class_name || ""; visible: showClassColumn; Layout.preferredWidth: 80; elide: Text.ElideRight }
                                Label { text: modelData.full_name || (modelData.first_name + " " + modelData.last_name); Layout.fillWidth: true }
                                Label { text: modelData.gender; visible: showGenderColumn; Layout.preferredWidth: 55 }
                                Label { text: modelData.source_school || ""; visible: showSchoolColumn; Layout.preferredWidth: 90; elide: Text.ElideRight }
                                Label { text: formatValue(modelData.average_grade, ""); visible: showGradeColumn; Layout.preferredWidth: 65 }
                                Label { text: formatValue(modelData.math_grade, ""); visible: showMathColumn; Layout.preferredWidth: 55 }
                                Label { text: formatValue(modelData.english_grade, ""); visible: showEnglishColumn; Layout.preferredWidth: 55 }
                                Label { text: formatValue(modelData.hebrew_grade, ""); visible: showHebrewColumn; Layout.preferredWidth: 55 }
                                Label { text: modelData.behavior_score || ""; visible: showBehaviorColumn; Layout.preferredWidth: 80; elide: Text.ElideRight }
                                Label { text: modelData.requested_friends ? (modelData.got_friend ? "קיבל: " + (modelData.got_friends || "-") : "חסר: " + modelData.requested_friends) : "-"; visible: showFriendsColumn; Layout.preferredWidth: 120; elide: Text.ElideRight; color: modelData.got_friend ? "#067647" : "#b54708" }
                                Label { text: modelData.requested_by || ""; visible: showRequestedByColumn; Layout.preferredWidth: 120; elide: Text.ElideRight }
                                Label { text: modelData.notes_summary || ""; visible: showNotesColumn; Layout.preferredWidth: 140; elide: Text.ElideRight }
                                Label { text: modelData.constraints_summary || ""; visible: showConstraintsColumn; Layout.preferredWidth: 140; elide: Text.ElideRight }
                                ComboBox {
                                    id: rowMoveClass
                                    model: classes
                                    textRole: "name"
                                    valueRole: "id"
                                    Layout.preferredWidth: 130
                                    currentIndex: classIndexById(modelData.class_id)
                                }
                                Button {
                                    text: "העבר"
                                    Layout.preferredWidth: 70
                                    enabled: rowMoveClass.currentValue !== undefined && rowMoveClass.currentValue !== modelData.class_id
                                    onClicked: {
                                        bridge.moveStudent(modelData.student_id, rowMoveClass.currentValue, false)
                                    }
                                }
                            }
                            MouseArea {
                                id: dragArea
                                anchors.fill: parent
                                drag.target: studentRow
                                onClicked: chooseStudent(modelData.student_id)
                                onReleased: {
                                    studentRow.Drag.drop()
                                    studentRow.x = 0
                                    studentRow.y = 0
                                }
                            }
                        }
                    }
                    Label {
                        visible: !hasAssignment() || visibleStudents().length === 0
                        text: !hasAssignment() ? "הריצו שיבוץ כדי לראות תלמידים לפי כיתה." : "אין תלמידים שמתאימים לחיפוש או לסינון הנוכחי."
                        color: "#667085"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }

                Panel {
                    Layout.preferredWidth: 430
                    Layout.minimumHeight: implicitHeight
                    Label { text: "כרטיס תלמיד"; font.bold: true; font.pixelSize: 18 }
                    Label {
                        text: selectedDetails.student ? selectedDetails.student.full_name : "בחרו תלמיד/ה"
                        font.pixelSize: 20
                        font.bold: true
                        color: "#172033"
                    }
                    Label {
                        text: selectedDetails.assignment ? ("כיתה נוכחית: " + selectedDetails.assignment.class_name) : ""
                        color: "#475467"
                    }
                    Label {
                        text: selectedDetails.student ? ("מגדר: " + selectedDetails.student.gender + " · בית ספר: " + selectedDetails.student.source_school + " · ממוצע: " + (selectedDetails.student.average_grade || "")) : ""
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: selectedStudentId !== 0 && studentDetailsLoading
                        text: "טוען פרטי תלמיד..."
                        color: "#475467"
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: selectedStudentId !== 0 && studentDetailsError.length > 0
                        text: studentDetailsError
                        color: "#b42318"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: studentDetailsReady()
                        text: "חברים שביקש/ה: " + (selectedDetails.requested_friends && selectedDetails.requested_friends.length ? selectedDetails.requested_friends.join(", ") : "אין")
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: studentDetailsReady()
                        text: "חברים שקיבל/ה: " + (selectedDetails.got_friends && selectedDetails.got_friends.length ? selectedDetails.got_friends.join(", ") : "אין כרגע")
                        color: selectedDetails.got_friends && selectedDetails.got_friends.length ? "#067647" : "#b54708"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: studentDetailsReady()
                        text: "מי ביקש/ה אותו/ה: " + (selectedDetails.requested_by && selectedDetails.requested_by.length ? selectedDetails.requested_by.join(", ") : "אין")
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    GridLayout {
                        visible: studentDetailsReady()
                        columns: 2
                        columnSpacing: 8
                        rowSpacing: 8
                        Layout.fillWidth: true
                        Label { text: "שם פרטי" }
                        TextField { id: editFirstName; text: selectedDetails.student ? selectedDetails.student.first_name : ""; Layout.fillWidth: true }
                        Label { text: "שם משפחה" }
                        TextField { id: editLastName; text: selectedDetails.student ? selectedDetails.student.last_name : ""; Layout.fillWidth: true }
                        Label { text: "שם מלא" }
                        TextField { id: editFullName; text: selectedDetails.student ? selectedDetails.student.full_name : ""; Layout.fillWidth: true }
                        Label { text: "מגדר" }
                        ComboBox { id: editGender; model: ["", "בן", "בת"]; currentIndex: Math.max(0, model.indexOf(selectedDetails.student ? selectedDetails.student.gender : "")); Layout.fillWidth: true }
                        Label { text: "בית ספר" }
                        TextField { id: editSourceSchool; text: selectedDetails.student ? selectedDetails.student.source_school : ""; Layout.fillWidth: true }
                        Label { text: "ממוצע" }
                        TextField { id: editAverageGrade; text: selectedDetails.student && selectedDetails.student.average_grade ? String(selectedDetails.student.average_grade) : ""; Layout.fillWidth: true }
                        Label { text: "התנהגות" }
                        ComboBox { id: editBehavior; model: ["", "גבוהה", "בינונית", "נמוכה"]; currentIndex: Math.max(0, model.indexOf(selectedDetails.student ? selectedDetails.student.behavior_score : "")); Layout.fillWidth: true }
                        Label { text: "דומיננטיות" }
                        TextField { id: editDominance; text: selectedDetails.student && selectedDetails.student.dominance_score ? String(selectedDetails.student.dominance_score) : ""; Layout.fillWidth: true }
                    }
                    Button {
                        visible: studentDetailsReady()
                        text: "שמירת תיקון נתוני תלמיד"
                        onClicked: {
                            selectedDetails = bridge.updateStudent(selectedStudentId, {
                                "first_name": editFirstName.text,
                                "last_name": editLastName.text,
                                "full_name": editFullName.text,
                                "gender": editGender.currentText,
                                "source_school": editSourceSchool.text,
                                "average_grade": editAverageGrade.text,
                                "behavior_score": editBehavior.currentText,
                                "dominance_score": editDominance.text
                            })
                        }
                    }
                    SectionTitle { text: "אילוצים אישיים"; visible: studentDetailsReady() }
                    GridLayout {
                        visible: studentDetailsReady()
                        columns: 2
                        columnSpacing: 8
                        rowSpacing: 8
                        Layout.fillWidth: true
                        Label { text: "כיתות מותרות" }
                        TextField { id: editAllowedClasses; text: selectedDetails.constraints ? selectedDetails.constraints.allowed_classes : ""; placeholderText: "ז׳1, ז׳2"; Layout.fillWidth: true }
                        Label { text: "כיתות אסורות" }
                        TextField { id: editForbiddenClasses; text: selectedDetails.constraints ? selectedDetails.constraints.forbidden_classes : ""; placeholderText: "ז׳3"; Layout.fillWidth: true }
                        Label { text: "חייב/ת עם" }
                        TextField { id: editMustWith; text: selectedDetails.constraints ? selectedDetails.constraints.must_be_with : ""; placeholderText: "שם תלמיד/ה"; Layout.fillWidth: true }
                        Label { text: "אסור עם" }
                        TextField { id: editMustNotWith; text: selectedDetails.constraints ? selectedDetails.constraints.must_not_be_with : ""; placeholderText: "שם תלמיד/ה"; Layout.fillWidth: true }
                    }
                    Button {
                        visible: studentDetailsReady()
                        text: "שמירת אילוצים אישיים"
                        onClicked: {
                            selectedDetails = bridge.updateStudentConstraints(
                                selectedStudentId,
                                editAllowedClasses.text,
                                editForbiddenClasses.text,
                                editMustWith.text,
                                editMustNotWith.text
                            )
                        }
                    }
                    Label { text: "הערות"; font.bold: true; visible: studentDetailsReady() && selectedDetails.notes && (selectedDetails.notes.parent || selectedDetails.notes.teacher || selectedDetails.notes.interview) }
                    Label {
                        visible: studentDetailsReady() && selectedDetails.notes && (selectedDetails.notes.parent || selectedDetails.notes.teacher || selectedDetails.notes.interview)
                        text: (selectedDetails.notes.parent ? "הורים: " + selectedDetails.notes.parent + "\n" : "") + (selectedDetails.notes.teacher ? "מורה: " + selectedDetails.notes.teacher + "\n" : "") + (selectedDetails.notes.interview ? "ראיון: " + selectedDetails.notes.interview : "")
                        color: "#475467"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Label { text: "סיבת שיבוץ"; font.bold: true; visible: studentDetailsReady() }
                    Repeater {
                        model: selectedDetails.reasons || []
                        Label {
                            text: "• " + modelData
                            wrapMode: Text.WordWrap
                            color: "#172033"
                            Layout.fillWidth: true
                        }
                    }
                    RowLayout {
                        visible: studentDetailsReady()
                        Label { text: "העבר/י ל"; Layout.preferredWidth: 80 }
                        ComboBox {
                            id: moveClassCombo
                            Layout.fillWidth: true
                            textRole: "name"
                            valueRole: "id"
                            model: classes
                        }
                    }
                    RowLayout {
                        visible: studentDetailsReady()
                        Button {
                            text: "העברה"
                            highlighted: true
                            enabled: selectedStudentId !== 0 && hasAssignment() && moveClassCombo.currentValue !== undefined
                            onClicked: {
                                bridge.moveStudent(selectedStudentId, moveClassCombo.currentValue, false)
                            }
                        }
                        Button {
                            text: "העברה ונעילה"
                            enabled: selectedStudentId !== 0 && hasAssignment() && moveClassCombo.currentValue !== undefined
                            onClicked: {
                                bridge.moveStudent(selectedStudentId, moveClassCombo.currentValue, true)
                            }
                        }
                        Button {
                            text: selectedDetails.assignment && selectedDetails.assignment.locked_manually ? "ביטול נעילה" : "נעילה"
                            enabled: selectedStudentId !== 0 && hasAssignment()
                            onClicked: {
                                var isLocked = selectedDetails.assignment && selectedDetails.assignment.locked_manually
                                bridge.setStudentLock(selectedStudentId, !isLocked)
                            }
                        }
                    }
                    RowLayout {
                        visible: studentDetailsReady()
                        Label { text: "החלפה עם"; Layout.preferredWidth: 90 }
                        ComboBox {
                            id: swapStudentCombo
                            Layout.fillWidth: true
                            model: dashboard.has_assignment ? dashboard.rows : []
                            textRole: "full_name"
                            valueRole: "student_id"
                        }
                        Button {
                            text: "החלפה"
                            enabled: selectedStudentId !== 0 && hasAssignment() && swapStudentCombo.currentValue !== undefined && swapStudentCombo.currentValue !== selectedStudentId
                            onClicked: {
                                if (swapStudentCombo.currentValue && swapStudentCombo.currentValue !== selectedStudentId) {
                                    bridge.swapStudents(selectedStudentId, swapStudentCombo.currentValue)
                                }
                            }
                        }
                    }
                    Label { text: "החלפה חכמה"; font.bold: true; visible: studentDetailsReady() }
                    ListView {
                        visible: studentDetailsReady()
                        Layout.fillWidth: true
                        Layout.preferredHeight: 170
                        clip: true
                        model: selectedDetails.suggestions || []
                        delegate: Rectangle {
                            width: ListView.view.width
                            height: 56
                            radius: 6
                            color: modelData.delta > 0 ? "#ecfdf3" : "#fff7ed"
                            border.color: modelData.delta > 0 ? "#079455" : "#f79009"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                Label { text: modelData.action + " · שינוי ציון: " + modelData.delta; font.bold: true }
                                Label { text: modelData.cost; color: "#475467"; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                        }
                    }
                }
            }
        }
    }

    component ResultsPage: PageShell {
        title: "תוצאות"
        subtitle: "בדיקת השיבוץ, נתוני כיתה מפורטים, טבלת תלמידים מלאה והעברות ידניות."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 16

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                MetricCard {
                    title: "ציון שיבוץ"
                    value: dashboard.has_assignment ? String(dashboard.score.total_score) : "-"
                    detail: dashboard.has_assignment ? "גרסה פעילה" : "אין שיבוץ"
                    accent: dashboard.has_assignment && dashboard.score.total_score >= 90 ? "#067647" : (dashboard.has_assignment && dashboard.score.total_score >= 75 ? "#b54708" : "#d92d20")
                }
                MetricCard { title: "תלמידים"; value: dashboard.has_assignment ? String(dashboard.rows.length) : String(studentCount); detail: "משובצים"; accent: "#0f766e" }
                MetricCard { title: "כיתות"; value: String(classes.length); detail: "בפרויקט"; accent: "#175cd3" }
                MetricCard { title: "חברים חסרים"; value: dashboard.has_assignment && dashboard.score.friendship ? String((dashboard.score.friendship.missing || []).length) : "-"; detail: "בקשות שלא מולאו"; accent: "#b54708" }
                MetricCard { title: "כללים שנשברו"; value: dashboard.has_assignment ? String((dashboard.score.hard_violations || []).length) : "-"; detail: "מחייבים"; accent: "#d92d20" }
                Item { Layout.fillWidth: true }
            }

            Panel {
                Layout.preferredHeight: 136
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    Label {
                        text: dashboard.has_assignment ? dashboard.score.summary : "הריצו שיבוץ כדי לראות תוצאות."
                        font.pixelSize: 18
                        font.bold: true
                        color: "#172033"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    AppButton { text: "ביטול פעולה"; enabled: hasAssignment(); onClicked: bridge.undo() }
                    AppButton { text: "שחזור פעולה"; enabled: hasAssignment(); onClicked: bridge.redo() }
                }
                Label {
                    text: hasAssignment()
                        ? ("מקור השיבוץ: " + engineSourceText(dashboard.score) + " · AI חיצוני: " + aiAssignmentText())
                        : "השיבוץ נוצר בחישוב מקומי. AI חיצוני משמש רק לבדיקה/הסבר אנונימי, לא לקביעת כיתה."
                    color: "#475467"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }

            Panel {
                Layout.preferredHeight: 226
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "כיתות"; Layout.fillWidth: true }
                    AppButton {
                        text: "כל הכיתות"
                        checkable: true
                        checked: selectedClassId === 0
                        enabled: hasAssignment()
                        onClicked: chooseClass(0)
                    }
                }
                ScrollView {
                    id: classStrip
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
                    Row {
                        spacing: 10
                        Repeater {
                            model: dashboard.has_assignment ? dashboard.score.class_stats : []
                            Rectangle {
                                width: 252
                                height: 148
                                radius: 8
                                color: selectedClassId === modelData.class_id ? "#ecfdf3" : "#ffffff"
                                border.color: selectedClassId === modelData.class_id ? "#079455" : "#dde5ef"
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label { text: modelData.name; font.bold: true; font.pixelSize: 16; Layout.fillWidth: true; elide: Text.ElideRight; color: "#172033" }
                                        Button {
                                            text: "נתוני כיתה"
                                            Layout.preferredWidth: 104
                                            onClicked: openClassDetails(modelData.class_id)
                                        }
                                    }
                                    Label { text: modelData.size + " תלמידים · בנים/בנות " + modelData.boys + "/" + modelData.girls; color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "ממוצע " + formatValue(modelData.avg_grade) + " · מת׳ " + formatValue(modelData.math_avg) + " · אנ׳ " + formatValue(modelData.english_avg) + " · עב׳ " + formatValue(modelData.hebrew_avg); color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "התנהגות: " + counterText(modelData.behavior_counts); color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "בתי ספר: " + modelData.source_school_count + " · חברים חסרים: " + modelData.friends_missing; color: modelData.friends_missing > 0 ? "#b54708" : "#067647"; Layout.fillWidth: true; elide: Text.ElideRight }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    acceptedButtons: Qt.RightButton
                                    onClicked: chooseClass(modelData.class_id)
                                }
                            }
                        }
                    }
                }
            }

            Panel {
                visible: selectedClassId !== 0 && selectedClassStats && selectedClassStats.name
                Layout.preferredHeight: 238
                SectionTitle { text: "נתוני כיתה מפורטים: " + (selectedClassStats.name || "") }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 18
                    rowSpacing: 8
                    Label { text: "גודל"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.size); Layout.fillWidth: true }
                    Label { text: "מגדר"; font.bold: true }
                    Label { text: counterText(selectedClassStats.gender_counts); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "ממוצע כללי"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.avg_grade); Layout.fillWidth: true }
                    Label { text: "מקצועות"; font.bold: true }
                    Label { text: "מת׳ " + formatValue(selectedClassStats.math_avg) + " · אנ׳ " + formatValue(selectedClassStats.english_avg) + " · עב׳ " + formatValue(selectedClassStats.hebrew_avg); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "התנהגות"; font.bold: true }
                    Label { text: counterText(selectedClassStats.behavior_counts) + " · ממוצע " + formatValue(selectedClassStats.avg_behavior); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "בתי ספר מקור"; font.bold: true }
                    Label { text: counterText(selectedClassStats.schools); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "חברים"; font.bold: true }
                    Label { text: selectedClassStats.friends_satisfied + " קיבלו · " + selectedClassStats.friends_missing + " חסרים · " + selectedClassStats.total_with_friend_requests + " ביקשו"; Layout.fillWidth: true }
                    Label { text: "דומיננטיות"; font.bold: true }
                    Label { text: formatValue(selectedClassStats.dominance_total); Layout.fillWidth: true }
                }
            }

            Panel {
                Layout.preferredHeight: 620
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle {
                        text: hasAssignment() ? ("תלמידים - " + selectedClassName() + " (" + visibleStudents().length + ")") : "אין שיבוץ פעיל"
                        Layout.fillWidth: true
                    }
                    TextField {
                        placeholderText: "חיפוש שם, קוד, כיתה או חבר"
                        text: studentSearchText
                        onTextChanged: studentSearchText = text
                        Layout.preferredWidth: 260
                    }
                    ComboBox {
                        model: ["הכל", "נעולים", "שונו ידנית", "בנים", "בנות", "ללא חבר"]
                        currentIndex: Math.max(0, model.indexOf(studentFilterMode))
                        onActivated: studentFilterMode = currentText
                        Layout.preferredWidth: 150
                    }
                }

                ScrollView {
                    id: resultTable
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: Math.max(1260, availableWidth)
                    Column {
                        width: Math.max(1260, resultTable.availableWidth)
                        spacing: 0
                        Rectangle {
                            width: parent.width
                            height: 38
                            color: "#eef4ff"
                            border.color: "#c7d7fe"
                            Row {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 8
                                TableHeader { textValue: "קוד"; cellWidth: 76 }
                                TableHeader { textValue: "שם"; cellWidth: 190 }
                                TableHeader { textValue: "כיתה"; cellWidth: 90 }
                                TableHeader { textValue: "מגדר"; cellWidth: 58 }
                                TableHeader { textValue: "בית ספר"; cellWidth: 118 }
                                TableHeader { textValue: "ממוצע"; cellWidth: 70 }
                                TableHeader { textValue: "מת׳"; cellWidth: 58 }
                                TableHeader { textValue: "אנ׳"; cellWidth: 58 }
                                TableHeader { textValue: "עב׳"; cellWidth: 58 }
                                TableHeader { textValue: "התנהגות"; cellWidth: 92 }
                                TableHeader { textValue: "חברים"; cellWidth: 168 }
                                TableHeader { textValue: "סטטוס"; cellWidth: 100 }
                                TableHeader { textValue: "העברה"; cellWidth: 250 }
                            }
                        }
                        Repeater {
                            model: hasAssignment() ? visibleStudents() : []
                            Rectangle {
                                width: parent.width
                                height: 48
                                color: selectedStudentId === modelData.student_id ? "#f0f9ff" : (index % 2 === 0 ? "#ffffff" : "#f8fafc")
                                border.color: selectedStudentId === modelData.student_id ? "#38bdf8" : "#e5e7eb"
                                MouseArea {
                                    anchors.fill: parent
                                    z: 0
                                    acceptedButtons: Qt.LeftButton
                                    onClicked: chooseStudent(modelData.student_id)
                                }
                                Row {
                                    z: 1
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 8
                                    TableCell { textValue: modelData.internal_code; cellWidth: 76; muted: true }
                                    TableCell { textValue: modelData.full_name || (modelData.first_name + " " + modelData.last_name); cellWidth: 190; bold: true }
                                    TableCell { textValue: modelData.class_name || ""; cellWidth: 90 }
                                    TableCell { textValue: modelData.gender || ""; cellWidth: 58 }
                                    TableCell { textValue: modelData.source_school || ""; cellWidth: 118 }
                                    TableCell { textValue: formatValue(modelData.average_grade, ""); cellWidth: 70 }
                                    TableCell { textValue: formatValue(modelData.math_grade, ""); cellWidth: 58 }
                                    TableCell { textValue: formatValue(modelData.english_grade, ""); cellWidth: 58 }
                                    TableCell { textValue: formatValue(modelData.hebrew_grade, ""); cellWidth: 58 }
                                    TableCell { textValue: modelData.behavior_score || ""; cellWidth: 92 }
                                    TableCell {
                                        textValue: modelData.requested_friends ? (modelData.got_friend ? "קיבל: " + (modelData.got_friends || "-") : "חסר: " + modelData.requested_friends) : "-"
                                        cellWidth: 168
                                        accent: modelData.got_friend ? "#067647" : "#b54708"
                                    }
                                    TableCell { textValue: modelData.locked_manually ? "נעול" : (modelData.changed_manually ? "שונה" : "רגיל"); cellWidth: 100; accent: modelData.locked_manually ? "#175cd3" : (modelData.changed_manually ? "#7e22ce" : "#475467") }
                                    Row {
                                        width: 250
                                        height: parent.height
                                        spacing: 6
                                        ComboBox {
                                            id: rowTargetClass
                                            width: 146
                                            height: 32
                                            model: classes
                                            textRole: "name"
                                            valueRole: "id"
                                            currentIndex: classIndexById(modelData.class_id)
                                        }
                                        Button {
                                            width: 72
                                            height: 32
                                            text: "העבר"
                                            enabled: classIdAt(rowTargetClass.currentIndex) > 0 && classIdAt(rowTargetClass.currentIndex) !== parseInt(modelData.class_id)
                                            onClicked: {
                                                bridge.moveStudent(parseInt(modelData.student_id), classIdAt(rowTargetClass.currentIndex), false)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Panel {
                Layout.preferredHeight: selectedStudentId === 0 ? 120 : 360
                SectionTitle { text: selectedStudentId === 0 ? "כרטיס תלמיד" : "כרטיס תלמיד: " + (selectedDetails.student ? selectedDetails.student.full_name : "") }
                Label {
                    visible: selectedStudentId === 0
                    text: "בחרו תלמיד בטבלה כדי לראות חברים, אילוצים, סיבות שיבוץ והצעות החלפה."
                    color: "#667085"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                Label {
                    visible: selectedStudentId !== 0 && studentDetailsLoading
                    text: "טוען פרטי תלמיד..."
                    color: "#475467"
                    Layout.fillWidth: true
                }
                Label {
                    visible: selectedStudentId !== 0 && studentDetailsError.length > 0
                    text: studentDetailsError
                    color: "#b42318"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                GridLayout {
                    visible: studentDetailsReady()
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 14
                    rowSpacing: 8
                    Label { text: "כיתה נוכחית"; font.bold: true }
                    Label { text: selectedDetails.assignment ? selectedDetails.assignment.class_name : "-"; Layout.fillWidth: true }
                    Label { text: "חברים שביקש/ה"; font.bold: true }
                    Label { text: selectedDetails.requested_friends && selectedDetails.requested_friends.length ? selectedDetails.requested_friends.join(", ") : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "חברים שקיבל/ה"; font.bold: true }
                    Label { text: selectedDetails.got_friends && selectedDetails.got_friends.length ? selectedDetails.got_friends.join(", ") : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "מי ביקש/ה אותו/ה"; font.bold: true }
                    Label { text: selectedDetails.requested_by && selectedDetails.requested_by.length ? selectedDetails.requested_by.join(", ") : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
                RowLayout {
                    visible: studentDetailsReady()
                    Layout.fillWidth: true
                    ComboBox {
                        id: selectedMoveClass
                        model: classes
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: selectedDetails.assignment ? classIndexById(selectedDetails.assignment.class_id) : 0
                        Layout.preferredWidth: 180
                    }
                    Button { text: "העבר"; highlighted: true; enabled: classIdAt(selectedMoveClass.currentIndex) > 0 && studentDetailsReady(); onClicked: bridge.moveStudent(parseInt(selectedStudentId), classIdAt(selectedMoveClass.currentIndex), false) }
                    Button { text: "העבר ונעל"; enabled: classIdAt(selectedMoveClass.currentIndex) > 0 && studentDetailsReady(); onClicked: bridge.moveStudent(parseInt(selectedStudentId), classIdAt(selectedMoveClass.currentIndex), true) }
                    Button {
                        text: selectedDetails.assignment && selectedDetails.assignment.locked_manually ? "בטל נעילה" : "נעל"
                        enabled: studentDetailsReady()
                        onClicked: {
                            var isLocked = selectedDetails.assignment && selectedDetails.assignment.locked_manually
                            bridge.setStudentLock(selectedStudentId, !isLocked)
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                Label { visible: studentDetailsReady(); text: "סיבות שיבוץ"; font.bold: true }
                Flow {
                    visible: studentDetailsReady()
                    Layout.fillWidth: true
                    spacing: 8
                    Repeater {
                        model: selectedDetails.reasons || []
                        MiniBadge { textValue: modelData; badgeColor: "#f2f4f7"; textColor: "#344054" }
                    }
                }
            }

            Panel {
                Layout.preferredHeight: 470
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "העברות והחלפות אפשריות שה-AI מציע"; Layout.fillWidth: true }
                    AppButton {
                        text: aiActionData.status === "running" ? "AI בודק..." : "בקש הצעות AI"
                        highlighted: true
                        enabled: hasAssignment() && aiActionData.status !== "running"
                        onClicked: bridge.requestAiActionSuggestionsAsync()
                    }
                }
                Label {
                    text: aiActionData.message || (projectAiAllowed ? "AI עדיין לא בדק את ההרצה הפעילה." : "צריך לאשר שימוש ב-AI בהגדרות הפרויקט כדי לקבל הצעות AI.")
                    color: aiActionData.actions && aiActionData.actions.length > 0 ? "#067647" : "#667085"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    visible: !projectAiAllowed
                    text: "ללא הרשאת AI בפרויקט לא תישלח בקשה חיצונית. הפעולות המקומיות עדיין זמינות במסך האילוצים."
                    color: "#b54708"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                ListView {
                    visible: aiActionData.actions && aiActionData.actions.length > 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 10
                    model: aiActionData.actions || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 128
                        radius: 8
                        color: "#ffffff"
                        border.color: "#dde5ef"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.action || ""
                                    font.bold: true
                                    color: "#172033"
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    text: actionDeltaText(modelData)
                                    font.bold: true
                                    color: actionDeltaColor(modelData)
                                    Layout.preferredWidth: 64
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                AppButton {
                                    text: modelData.action_type === "swap" ? "בצע החלפה" : "בצע העברה"
                                    highlighted: true
                                    enabled: hasAssignment()
                                    Layout.preferredWidth: 120
                                    onClicked: applyActionCandidate(modelData)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                MiniBadge { textValue: "לפני " + formatValue(modelData.score_before); badgeColor: "#f2f4f7"; textColor: "#344054" }
                                MiniBadge { textValue: "אחרי " + formatValue(modelData.score_after); badgeColor: "#ecfdf3"; textColor: "#067647" }
                                MiniBadge { textValue: "שינוי " + actionDeltaText(modelData); badgeColor: "#ecfdf3"; textColor: "#067647" }
                                MiniBadge { textValue: "כללים " + formatValue(modelData.hard_after); badgeColor: Number(modelData.hard_after || 0) === 0 ? "#ecfdf3" : "#fee4e2"; textColor: Number(modelData.hard_after || 0) === 0 ? "#067647" : "#b42318" }
                                Item { Layout.fillWidth: true }
                            }
                            Label {
                                text: modelData.ai_reason ? ("נימוק AI: " + modelData.ai_reason) : (modelData.cost || "")
                                color: "#667085"
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }
    }

    component TeacherResultsPage: PageShell {
        title: "תוצאות השיבוץ"
        subtitle: "כאן בודקים את הכיתות שנוצרו, נכנסים לכיתה מסוימת או מציגים את כל התלמידים, ומבצעים תיקונים ידניים פשוטים."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 16

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                MetricCard {
                    title: "ציון כללי"
                    value: hasAssignment() ? scoreText(dashboard.score.total_score) : "-"
                    detail: hasAssignment() ? "מדד עזר, לא אמת מדעית" : "אין עדיין שיבוץ"
                    accent: hasAssignment() && dashboard.score.total_score >= 90 ? "#067647" : (hasAssignment() && dashboard.score.total_score >= 75 ? "#b54708" : "#d92d20")
                }
                MetricCard { title: "תלמידים"; value: hasAssignment() ? String(dashboard.rows.length) : String(studentCount); detail: hasAssignment() ? "שובצו לכיתות" : "מחכים להרצה"; accent: "#0f766e" }
                MetricCard { title: "כיתות"; value: String(classes.length); detail: "בפרויקט הפעיל"; accent: "#175cd3" }
                MetricCard {
                    title: "בקשות חברים"
                    value: hasAssignment() && dashboard.score.friendship ? percentageText((dashboard.score.friendship.satisfied || []).length, dashboard.score.friendship.total_with_requests) : "-"
                    detail: hasAssignment() ? "קיבלו לפחות חבר אחד" : "יוצג אחרי הרצה"
                    accent: "#7e22ce"
                }
                MetricCard {
                    title: "כללים מחייבים"
                    value: hasAssignment() ? String((dashboard.score.hard_violations || []).length) : "-"
                    detail: "צריך להיות 0"
                    accent: hasAssignment() && (dashboard.score.hard_violations || []).length === 0 ? "#067647" : "#d92d20"
                }
                Item { Layout.fillWidth: true }
            }

            Panel {
                Layout.preferredHeight: 248
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: hasAssignment() ? dashboard.score.summary : "עדיין אין תוצאות. עברו למסך ההרצה ולחצו על הרצת שיבוץ."
                        font.pixelSize: 18
                        font.bold: true
                        color: "#172033"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    AppButton {
                        text: "ביטול פעולה"
                        enabled: hasAssignment()
                        onClicked: {
                            bridge.undo()
                        }
                    }
                    AppButton {
                        text: "שחזור פעולה"
                        enabled: hasAssignment()
                        onClicked: {
                            bridge.redo()
                        }
                    }
                }
                Label {
                    text: hasAssignment()
                        ? "הציון מסכם איזונים כמו גודל כיתה, חברים, ציונים והתנהגות. כלל מחייב שנשבר חשוב יותר מכל ציון, ולכן אם יש כזה צריך לתקן אותו קודם."
                        : "השיבוץ מתבצע מקומית במחשב. AI, אם הופעל, משמש רק להסבר והמלצות ולא מזיז תלמידים."
                    color: "#475467"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Label { text: "הרצה פעילה"; font.bold: true; color: "#172033"; Layout.preferredWidth: 86 }
                    ComboBox {
                        id: resultVersionCombo
                        model: hasAssignment() ? dashboard.versions : []
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: activeVersionIndex()
                        enabled: hasAssignment() && dashboard.versions && dashboard.versions.length > 0
                        Layout.preferredWidth: 240
                        onActivated: {
                            if (currentValue) {
                                bridge.selectAssignmentVersion(currentValue)
                            }
                        }
                    }
                    MiniBadge { textValue: activeVersionScore(); badgeColor: "#eef4ff"; textColor: "#175cd3" }
                    TextField {
                        id: resultVersionName
                        text: activeVersionName()
                        placeholderText: "שם ההרצה"
                        enabled: hasAssignment()
                        Layout.preferredWidth: 220
                    }
                    AppButton {
                        text: "שמירת שם"
                        enabled: hasAssignment() && dashboard.version && resultVersionName.text.trim().length > 0
                        onClicked: {
                            var result = bridge.renameAssignmentVersion(dashboard.version.id, resultVersionName.text)
                            if (!result.ok) conflictAiOutput = result.message
                        }
                    }
                    AppButton {
                        text: "הרצה חדשה"
                        highlighted: true
                        enabled: hasProject()
                        onClicked: pageIndex = 5
                    }
                    Item { Layout.fillWidth: true }
                }
                Label {
                    text: hasAssignment() ? "אפשר לחזור למסך הכללים, לשנות משקלים/הגדרות ולהריץ שוב. כל הרצה נשמרת כאן כגרסה שאפשר לחזור אליה." : ""
                    color: "#667085"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    visible: hasAssignment()
                }
            }

            Panel {
                Layout.preferredHeight: 320
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "סקירת כיתות"; Layout.fillWidth: true }
                    AppButton {
                        text: "כל התלמידים"
                        highlighted: selectedClassId === 0
                        enabled: hasAssignment()
                        onClicked: chooseClass(0)
                    }
                }
                Label {
                    text: "לחצו על כיתה כדי לראות רק את תלמידי הכיתה. לחצו על פרטי כיתה כדי לפתוח חלון עם כל הנתונים המפורטים."
                    color: "#667085"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                ScrollView {
                    id: classOverviewScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    Flow {
                        width: Math.max(classOverviewScroll.availableWidth, 1)
                        spacing: 10
                        Repeater {
                            model: hasAssignment() && dashboard.score.class_stats ? dashboard.score.class_stats : []
                            Rectangle {
                                width: 286
                                height: 152
                                radius: 8
                                color: selectedClassId === modelData.class_id ? "#ecfdf3" : "#ffffff"
                                border.color: selectedClassId === modelData.class_id ? "#079455" : "#dde5ef"
                                MouseArea {
                                    anchors.fill: parent
                                    z: 0
                                    onClicked: chooseClass(modelData.class_id)
                                }
                                ColumnLayout {
                                    z: 1
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label { text: modelData.name; font.bold: true; font.pixelSize: 17; color: "#172033"; Layout.fillWidth: true; elide: Text.ElideRight }
                                        MiniBadge { textValue: String(modelData.size) + " תלמידים"; badgeColor: "#eef4ff"; textColor: "#175cd3" }
                                        MiniBadge { textValue: "ציון " + formatValue(modelData.quality_score); badgeColor: modelData.quality_score >= 85 ? "#ecfdf3" : (modelData.quality_score >= 70 ? "#fff7ed" : "#fee4e2"); textColor: modelData.quality_score >= 85 ? "#067647" : (modelData.quality_score >= 70 ? "#b54708" : "#b42318") }
                                    }
                                    Label { text: "ציון כיתה " + formatValue(modelData.quality_score) + " · " + (modelData.quality_summary || ""); color: modelData.quality_score >= 85 ? "#067647" : (modelData.quality_score >= 70 ? "#b54708" : "#d92d20"); Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "ממוצע " + formatValue(modelData.avg_grade) + " · בנים/בנות " + formatValue(modelData.boys, "0") + "/" + formatValue(modelData.girls, "0"); color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Label { text: "חברים: " + formatValue(modelData.friends_satisfied, "0") + " מולאו · " + formatValue(modelData.friends_missing, "0") + " חסרות"; color: modelData.friends_missing > 0 ? "#b54708" : "#067647"; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Item { Layout.fillHeight: true }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Button {
                                            text: "כניסה לכיתה"
                                            highlighted: selectedClassId === modelData.class_id
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 32
                                            onClicked: chooseClass(modelData.class_id)
                                        }
                                        Button {
                                            text: "פרטי כיתה"
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 32
                                            onClicked: openClassDetails(modelData.class_id)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Panel {
                visible: hasAssignment()
                Layout.preferredHeight: 760
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle {
                        text: "תלמידים - " + selectedClassName() + " (" + visibleStudents().length + ")"
                        Layout.fillWidth: true
                    }
                    TextField {
                        placeholderText: "חיפוש שם, קוד, כיתה או חבר"
                        text: studentSearchText
                        onTextChanged: studentSearchText = text
                        Layout.preferredWidth: 260
                    }
                    ComboBox {
                        model: ["הכל", "נעולים", "שונו ידנית", "בנים", "בנות", "ללא חבר"]
                        currentIndex: Math.max(0, model.indexOf(studentFilterMode))
                        onActivated: studentFilterMode = currentText
                        Layout.preferredWidth: 150
                    }
                }
                Label {
                    text: selectedClassId === 0 ? "מוצגים כל התלמידים מכל הכיתות. כדי להתמקד בכיתה אחת בחרו כיתה בכרטיסים למעלה." : "מוצגים רק תלמידי הכיתה שנבחרה. אפשר להעביר תלמיד לכיתה אחרת ישירות מהשורה."
                    color: "#667085"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                ScrollView {
                    id: teacherResultsTable
                    Layout.fillWidth: true
                    Layout.preferredHeight: 610
                    Layout.minimumHeight: 360
                    clip: true
                    contentWidth: Math.max(1340, availableWidth)
                    contentHeight: availableHeight
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOff
                    Item {
                        width: Math.max(1340, teacherResultsTable.availableWidth)
                        height: teacherResultsTable.availableHeight
                        Rectangle {
                            id: teacherResultsHeader
                            width: parent.width
                            height: 40
                            color: "#eef4ff"
                            border.color: "#c7d7fe"
                            Row {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 8
                                TableHeader { textValue: "קוד"; cellWidth: 70 }
                                TableHeader { textValue: "שם תלמיד"; cellWidth: 190 }
                                TableHeader { textValue: "כיתה"; cellWidth: 90 }
                                TableHeader { textValue: "מגדר"; cellWidth: 58 }
                                TableHeader { textValue: "בית ספר מקור"; cellWidth: 130 }
                                TableHeader { textValue: "ממוצע"; cellWidth: 70 }
                                TableHeader { textValue: "מתמטיקה"; cellWidth: 74 }
                                TableHeader { textValue: "אנגלית"; cellWidth: 64 }
                                TableHeader { textValue: "עברית"; cellWidth: 64 }
                                TableHeader { textValue: "התנהגות"; cellWidth: 96 }
                                TableHeader { textValue: "חברים"; cellWidth: 180 }
                                TableHeader { textValue: "מצב"; cellWidth: 96 }
                                TableHeader { textValue: "העברה לכיתה אחרת"; cellWidth: 250 }
                            }
                        }
                        ListView {
                            id: teacherStudentList
                            anchors.top: teacherResultsHeader.bottom
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            clip: true
                            model: visibleStudents()
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                            delegate: Rectangle {
                                width: ListView.view.width
                                height: 52
                                color: selectedStudentId === modelData.student_id ? "#f0f9ff" : (index % 2 === 0 ? "#ffffff" : "#f8fafc")
                                border.color: selectedStudentId === modelData.student_id ? "#38bdf8" : "#e5e7eb"
                                MouseArea {
                                    anchors.fill: parent
                                    z: 0
                                    onClicked: chooseStudent(modelData.student_id)
                                }
                                Row {
                                    z: 1
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 8
                                    TableCell { textValue: modelData.internal_code; cellWidth: 70; muted: true }
                                    TableCell { textValue: modelData.full_name || (modelData.first_name + " " + modelData.last_name); cellWidth: 190; bold: true }
                                    TableCell { textValue: modelData.class_name || ""; cellWidth: 90 }
                                    TableCell { textValue: modelData.gender || "-"; cellWidth: 58 }
                                    TableCell { textValue: modelData.source_school || "-"; cellWidth: 130 }
                                    TableCell { textValue: formatValue(modelData.average_grade, "-"); cellWidth: 70 }
                                    TableCell { textValue: formatValue(modelData.math_grade, "-"); cellWidth: 74 }
                                    TableCell { textValue: formatValue(modelData.english_grade, "-"); cellWidth: 64 }
                                    TableCell { textValue: formatValue(modelData.hebrew_grade, "-"); cellWidth: 64 }
                                    TableCell { textValue: modelData.behavior_score || "-"; cellWidth: 96 }
                                    FriendSlots { slots: modelData.friend_slots || []; cellWidth: 180 }
                                    TableCell { textValue: modelData.locked_manually ? "נעול" : (modelData.changed_manually ? "שונה ידנית" : "רגיל"); cellWidth: 96; accent: modelData.locked_manually ? "#175cd3" : (modelData.changed_manually ? "#7e22ce" : "#475467") }
                                    Row {
                                        width: 250
                                        height: parent.height
                                        spacing: 6
                                        ComboBox {
                                            id: teacherRowTargetClass
                                            width: 148
                                            height: 32
                                            model: classes
                                            textRole: "name"
                                            valueRole: "id"
                                            currentIndex: classIndexById(modelData.class_id)
                                        }
                                        Button {
                                            width: 76
                                            height: 32
                                            text: "העבר"
                                            enabled: classIdAt(teacherRowTargetClass.currentIndex) > 0 && classIdAt(teacherRowTargetClass.currentIndex) !== parseInt(modelData.class_id)
                                            onClicked: moveStudentToClass(modelData.student_id, classIdAt(teacherRowTargetClass.currentIndex), false)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Panel {
                Layout.preferredHeight: selectedStudentId === 0 ? 118 : 330
                SectionTitle { text: selectedStudentId === 0 ? "כרטיס תלמיד" : "כרטיס תלמיד: " + (selectedDetails.student ? selectedDetails.student.full_name : "") }
                Label {
                    visible: selectedStudentId === 0
                    text: "בחרו תלמיד בטבלה כדי לראות חברים שביקש, אילוצים, סיבות שיבוץ והצעות תיקון."
                    color: "#667085"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    visible: selectedStudentId !== 0 && studentDetailsLoading
                    text: "טוען פרטי תלמיד..."
                    color: "#475467"
                    Layout.fillWidth: true
                }
                Label {
                    visible: selectedStudentId !== 0 && studentDetailsError.length > 0
                    text: studentDetailsError
                    color: "#b42318"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                GridLayout {
                    visible: selectedStudentId !== 0 && !studentDetailsLoading && studentDetailsError.length === 0
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 14
                    rowSpacing: 8
                    Label { text: "כיתה נוכחית"; font.bold: true }
                    Label { text: selectedDetails.assignment ? selectedDetails.assignment.class_name : "-"; Layout.fillWidth: true }
                    Label { text: "חברים שביקש/ה"; font.bold: true }
                    Label { text: selectedDetails.requested_friends && selectedDetails.requested_friends.length ? selectedDetails.requested_friends.join(", ") : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "חברים שקיבל/ה"; font.bold: true }
                    Label { text: selectedDetails.got_friends && selectedDetails.got_friends.length ? selectedDetails.got_friends.join(", ") : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "אילוצים"; font.bold: true }
                    Label { text: selectedDetails.assignment ? (selectedDetails.assignment.constraints_summary || "-") : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
                RowLayout {
                    visible: selectedStudentId !== 0 && !studentDetailsLoading && studentDetailsError.length === 0
                    Layout.fillWidth: true
                    ComboBox {
                        id: teacherSelectedMoveClass
                        model: classes
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: selectedDetails.assignment ? classIndexById(selectedDetails.assignment.class_id) : 0
                        Layout.preferredWidth: 200
                    }
                    Button { text: "העבר לכיתה"; highlighted: true; enabled: classIdAt(teacherSelectedMoveClass.currentIndex) > 0 && !studentDetailsLoading; onClicked: moveStudentToClass(selectedStudentId, classIdAt(teacherSelectedMoveClass.currentIndex), false) }
                    Button { text: "העבר ונעל"; enabled: classIdAt(teacherSelectedMoveClass.currentIndex) > 0 && !studentDetailsLoading; onClicked: moveStudentToClass(selectedStudentId, classIdAt(teacherSelectedMoveClass.currentIndex), true) }
                    Button {
                        text: selectedDetails.assignment && selectedDetails.assignment.locked_manually ? "בטל נעילה" : "נעל במקום"
                        enabled: selectedStudentId !== 0 && !studentDetailsLoading
                        onClicked: {
                            var isLocked = selectedDetails.assignment && selectedDetails.assignment.locked_manually
                            bridge.setStudentLock(selectedStudentId, !isLocked)
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                Label { visible: selectedStudentId !== 0 && !studentDetailsLoading && studentDetailsError.length === 0; text: "הסברים לשיבוץ"; font.bold: true }
                Flow {
                    visible: selectedStudentId !== 0 && !studentDetailsLoading && studentDetailsError.length === 0
                    Layout.fillWidth: true
                    spacing: 8
                    Repeater {
                        model: selectedDetails.reasons || []
                        MiniBadge { textValue: modelData; badgeColor: "#f2f4f7"; textColor: "#344054" }
                    }
                }
            }
        }
    }

    component ConflictsPage: PageShell {
        property bool reportLoading: conflictsData.status === "running" || conflictsData.status === "not_loaded"
        property bool reportFailed: conflictsData.status === "failed"
        title: "אילוצים מתנגשים"
        subtitle: "כאן מוצגים כללים מחייבים שנשברו, בקשות חברים שלא מולאו ובידוד חברתי אפשרי, יחד עם החלפות והעברות שהמערכת ניקדה מראש."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                MetricCard { title: "אילוצים/בעיות"; value: String((conflictsData.conflicts || []).length); detail: "דורשים בדיקה"; accent: "#d92d20" }
                MetricCard { title: "פעולות אפשריות"; value: String((conflictsData.action_candidates || []).length); detail: "עם ציון לפני/אחרי"; accent: "#0f766e" }
                Item { Layout.fillWidth: true }
            }
            Panel {
                visible: reportLoading || reportFailed
                Layout.preferredHeight: reportFailed ? 132 : 104
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    BusyIndicator {
                        running: reportLoading
                        visible: reportLoading
                        Layout.preferredWidth: 32
                        Layout.preferredHeight: 32
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label {
                            text: reportFailed ? "טעינת דוח האילוצים נכשלה" : "טוען דוח אילוצים"
                            font.bold: true
                            color: reportFailed ? "#b42318" : "#172033"
                            Layout.fillWidth: true
                        }
                        Label {
                            text: conflictsData.message || "בודק התנגשויות ומחשב הצעות תיקון ברקע."
                            wrapMode: Text.WordWrap
                            color: "#667085"
                            Layout.fillWidth: true
                        }
                    }
                    AppButton {
                        visible: reportFailed
                        text: "נסה שוב"
                        highlighted: true
                        Layout.preferredWidth: 112
                        onClicked: {
                            bridge.loadConflictsReportAsync()
                            conflictsData = bridge.conflictsReport()
                        }
                    }
                }
            }
            Panel {
                Layout.preferredHeight: 360
                SectionTitle { text: "רשימת התנגשויות" }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: conflictsData.conflicts || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 64
                        radius: 6
                        color: modelData.severity === "critical" ? "#fee4e2" : "#fff7ed"
                        border.color: modelData.severity === "critical" ? "#d92d20" : "#f79009"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Label { text: modelData.message; font.bold: true; color: "#172033"; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label { text: modelData.reason; color: "#475467"; Layout.fillWidth: true; elide: Text.ElideRight }
                        }
                    }
                }
            }
            Panel {
                Layout.preferredHeight: 470
                SectionTitle { text: "החלפות והעברות אפשריות" }
                Label {
                    visible: !reportLoading && !reportFailed && (conflictsData.action_candidates || []).length === 0
                    text: "לא נמצאה פעולה ידנית ברורה שמשפרת את הציון בלי להוסיף בעיות. אפשר להריץ שוב עם יותר סידורים או לבדוק תלמידים ידנית."
                    wrapMode: Text.WordWrap
                    color: "#667085"
                    Layout.fillWidth: true
                }
                ListView {
                    visible: !reportLoading && !reportFailed && (conflictsData.action_candidates || []).length > 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 10
                    model: conflictsData.action_candidates || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 118
                        radius: 8
                        color: "#ffffff"
                        border.color: "#dde5ef"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.action || ""
                                    font.bold: true
                                    color: "#172033"
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    text: actionDeltaText(modelData)
                                    font.bold: true
                                    color: actionDeltaColor(modelData)
                                    Layout.preferredWidth: 64
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                AppButton {
                                    text: modelData.action_type === "swap" ? "בצע החלפה" : "בצע העברה"
                                    highlighted: Number(modelData.delta || 0) >= 0
                                    enabled: hasAssignment()
                                    Layout.preferredWidth: 120
                                    onClicked: applyActionCandidate(modelData)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                MiniBadge { textValue: "לפני " + formatValue(modelData.score_before); badgeColor: "#f2f4f7"; textColor: "#344054" }
                                MiniBadge { textValue: "אחרי " + formatValue(modelData.score_after); badgeColor: Number(modelData.delta || 0) >= 0 ? "#ecfdf3" : "#fee4e2"; textColor: Number(modelData.delta || 0) >= 0 ? "#067647" : "#b42318" }
                                MiniBadge { textValue: "כללים " + formatValue(modelData.hard_after); badgeColor: Number(modelData.hard_after || 0) === 0 ? "#ecfdf3" : "#fee4e2"; textColor: Number(modelData.hard_after || 0) === 0 ? "#067647" : "#b42318" }
                                Item { Layout.fillWidth: true }
                            }
                            Label {
                                text: modelData.cost || ""
                                color: "#667085"
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
            Panel {
                Layout.preferredHeight: aiActionData.actions && aiActionData.actions.length > 0 ? 430 : 190
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "הצעות AI להחלפה מלאה"; Layout.fillWidth: true }
                    AppButton {
                        text: aiActionData.status === "running" ? "בודק רעיונות..." : "בקש רעיונות שיפור מ-AI"
                        enabled: hasAssignment() && aiActionData.status !== "running" && !reportLoading
                        highlighted: true
                        Layout.preferredWidth: 180
                        onClicked: {
                            conflictAiOutput = ""
                            var result = bridge.requestConflictAiActionSuggestionsAsync()
                            aiActionData = bridge.aiActionSuggestionsData()
                            if (!result.started && result.message) conflictAiOutput = result.message
                        }
                    }
                }
                Label {
                    text: aiActionData.message || (projectAiAllowed ? "AI עדיין לא בדק הצעות החלפה להרצה הפעילה." : "ללא הרשאת AI בפרויקט, תוצג בחירה מקומית מתוך פעולות שנבדקו.")
                    color: aiActionData.actions && aiActionData.actions.length > 0 ? "#067647" : "#667085"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    visible: conflictAiOutput.length > 0 && (!aiActionData.message || aiActionData.message.length === 0)
                    text: conflictAiOutput
                    color: "#b42318"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    visible: !projectAiAllowed
                    text: "ללא הרשאת AI בפרויקט לא תישלח בקשה חיצונית; המערכת עדיין יכולה לבחור הצעות מתוך הפעולות המקומיות שנוקדו."
                    color: "#b54708"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                ListView {
                    visible: aiActionData.actions && aiActionData.actions.length > 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 10
                    model: aiActionData.actions || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 132
                        radius: 8
                        color: "#ffffff"
                        border.color: "#dde5ef"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.action || ""
                                    font.bold: true
                                    color: "#172033"
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    text: actionDeltaText(modelData)
                                    font.bold: true
                                    color: actionDeltaColor(modelData)
                                    Layout.preferredWidth: 64
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                AppButton {
                                    text: modelData.action_type === "swap" ? "בצע החלפה מלאה" : "בצע העברה מלאה"
                                    highlighted: true
                                    enabled: hasAssignment()
                                    Layout.preferredWidth: 150
                                    onClicked: applyActionCandidate(modelData)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                MiniBadge { textValue: "לפני " + formatValue(modelData.score_before); badgeColor: "#f2f4f7"; textColor: "#344054" }
                                MiniBadge { textValue: "אחרי " + formatValue(modelData.score_after); badgeColor: Number(modelData.delta || 0) >= 0 ? "#ecfdf3" : "#fee4e2"; textColor: Number(modelData.delta || 0) >= 0 ? "#067647" : "#b42318" }
                                MiniBadge { textValue: "שינוי " + actionDeltaText(modelData); badgeColor: Number(modelData.delta || 0) >= 0 ? "#ecfdf3" : "#fee4e2"; textColor: Number(modelData.delta || 0) >= 0 ? "#067647" : "#b42318" }
                                MiniBadge { textValue: "כללים " + formatValue(modelData.hard_after); badgeColor: Number(modelData.hard_after || 0) === 0 ? "#ecfdf3" : "#fee4e2"; textColor: Number(modelData.hard_after || 0) === 0 ? "#067647" : "#b42318" }
                                Item { Layout.fillWidth: true }
                            }
                            Label {
                                text: modelData.ai_reason ? ("נימוק AI: " + modelData.ai_reason) : (modelData.cost || "")
                                color: "#667085"
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }
    }

    component ReportsPage: PageShell {
        title: "דוח איכות"
        subtitle: "דוח מפורט על איכות השיבוץ, כולל פירוט מדדים, חריגות, פיזור וציוני כיתות."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                MetricCard { title: "ציון כללי"; value: qualityData.has_assignment ? String(qualityData.total_score) : "-"; detail: "מתוך 100"; accent: "#067647" }
                MetricCard { title: "ללא חבר"; value: String((qualityData.missing_friends || []).length); detail: "תלמידים"; accent: "#b54708" }
                MetricCard { title: "בידוד חברתי"; value: String((qualityData.isolated_students || []).length); detail: "בית ספר מקור"; accent: "#7e22ce" }
                MetricCard { title: "כללים שנשברו"; value: String((qualityData.hard_violations || []).length); detail: "מחייבים"; accent: "#d92d20" }
                Item { Layout.fillWidth: true }
            }
            AiReviewPanel {}
            Panel {
                SectionTitle { text: "תמונת שכבה" }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 20
                    rowSpacing: 8
                    Label { text: "תלמידים משובצים"; font.bold: true }
                    Label { text: qualityData.global_stats ? formatValue(qualityData.global_stats.assigned_count) + " מתוך " + formatValue(qualityData.global_stats.student_count) : "-"; Layout.fillWidth: true }
                    Label { text: "כיתות"; font.bold: true }
                    Label { text: qualityData.global_stats ? formatValue(qualityData.global_stats.class_count) : "-"; Layout.fillWidth: true }
                    Label { text: "ממוצע כללי / מתמטיקה / אנגלית / עברית"; font.bold: true }
                    Label { text: qualityData.global_stats ? formatValue(qualityData.global_stats.average_grade) + " / " + formatValue(qualityData.global_stats.math_average) + " / " + formatValue(qualityData.global_stats.english_average) + " / " + formatValue(qualityData.global_stats.hebrew_average) : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "מגדר"; font.bold: true }
                    Label { text: qualityData.global_stats ? counterText(qualityData.global_stats.gender_counts) : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "התנהגות"; font.bold: true }
                    Label { text: qualityData.global_stats ? counterText(qualityData.global_stats.behavior_counts) : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Label { text: "בתי ספר מקור"; font.bold: true }
                    Label { text: qualityData.global_stats ? counterText(qualityData.global_stats.source_school_counts) : "-"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
            }
            Panel {
                Layout.preferredHeight: 260
                SectionTitle { text: "סיכום למורה" }
                TextArea {
                    text: qualityData.teacher_summary || qualityData.manager_text || ""
                    readOnly: true
                    wrapMode: TextEdit.WordWrap
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                }
            }
            Panel {
                Layout.preferredHeight: 220
                SectionTitle { text: "ציון לפי כיתה" }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: qualityData.class_stats || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 46
                        color: index % 2 === 0 ? "#ffffff" : "#f8fafc"
                        border.color: "#e5e7eb"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 10
                            Label { text: modelData.name; font.bold: true; color: "#172033"; Layout.preferredWidth: 150; elide: Text.ElideRight }
                            ProgressBar { from: 0; to: 100; value: modelData.quality_score || 0; Layout.fillWidth: true }
                            Label { text: formatValue(modelData.quality_score); font.bold: true; color: modelData.quality_score >= 85 ? "#067647" : (modelData.quality_score >= 70 ? "#b54708" : "#d92d20"); Layout.preferredWidth: 54; horizontalAlignment: Text.AlignHCenter }
                            Label { text: modelData.quality_summary || ""; color: "#475467"; Layout.preferredWidth: 230; elide: Text.ElideRight }
                        }
                    }
                }
            }
            Panel {
                Layout.preferredHeight: 430
                SectionTitle { text: "מדדי איכות לפי נושא" }
                GridLayout {
                    Layout.fillWidth: true
                    columns: window.width > 1150 ? 2 : 1
                    columnSpacing: 12
                    rowSpacing: 12
                    Repeater {
                        model: qualityData.penalties ? Object.keys(qualityData.penalties) : []
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 104
                            radius: 8
                            color: "#ffffff"
                            border.color: "#dde5ef"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: penaltyLabel(modelData)
                                        font.bold: true
                                        color: "#172033"
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        text: String(qualityData.penalties[modelData])
                                        font.pixelSize: 22
                                        font.bold: true
                                        color: qualityData.penalties[modelData] <= 5 ? "#067647" : (qualityData.penalties[modelData] <= 15 ? "#b54708" : "#d92d20")
                                        Layout.preferredWidth: 72
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                                ProgressBar { from: 0; to: 100; value: qualityData.penalties[modelData]; Layout.fillWidth: true }
                                Label {
                                    text: penaltyHelp(modelData)
                                    color: "#667085"
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
            Panel {
                Layout.preferredHeight: reportAiOutput.length > 0 ? 720 : 300
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { text: "סיכום מפורט AI / מקומי"; Layout.fillWidth: true }
                    AppButton {
                        text: reportAiBusy ? "מכין סיכום..." : "צור סיכום"
                        enabled: hasProject() && !reportAiBusy
                        highlighted: true
                        Layout.preferredWidth: 132
                        onClicked: {
                            reportAiBusy = true
                            reportAiOutput = "מכין סיכום איכות. אפשר להמשיך לעבוד בזמן שהבקשה רצה..."
                            bridge.askAiAssistantAsync("report", "כתוב דוח מפורט אבל ברור לקריאה על ידי מורה. כלול שורה תחתונה, כמה ירד בכל מדד איכות, ציון כל כיתה, כמות תלמידים בכל כיתה, בנים/בנות אם קיים, ממוצעים, חברים חסרים, כללים שנשברו, ונקודות פעולה קצרות. אל תסתפק בסיכום כללי.", projectAiAllowed)
                        }
                    }
                }
                Label {
                    text: projectAiAllowed ? "הרשאת AI לפרויקט פעילה." : "הרשאת AI לפרויקט כבויה; הסיכום ייווצר מקומית."
                    color: projectAiAllowed ? "#067647" : "#b54708"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        id: aiReportText
                        width: parent.availableWidth
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.WordWrap
                        textFormat: TextEdit.PlainText
                        text: reportAiOutput.length > 0 ? reportAiOutput : "עדיין לא נוצר סיכום מפורט להרצה הפעילה."
                        color: reportAiOutput.length > 0 ? "#172033" : "#667085"
                        background: Rectangle {
                            color: "#f8fafc"
                            radius: 7
                            border.color: "#d0d5dd"
                        }
                    }
                }
            }
        }
    }

    component ComparePage: PageShell {
        title: "השוואת גרסאות"
        subtitle: "בחרו שתי גרסאות שיבוץ והשוו ציון, מדדי איכות ותלמידים שעברו כיתה."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 180
                RowLayout {
                    Layout.fillWidth: true
                    ComboBox { id: leftVersion; model: dashboard.has_assignment ? dashboard.versions : []; textRole: "name"; valueRole: "id"; Layout.fillWidth: true }
                    ComboBox { id: rightVersion; model: dashboard.has_assignment ? dashboard.versions : []; textRole: "name"; valueRole: "id"; Layout.fillWidth: true }
                    Button {
                        text: "השוואה"
                        highlighted: true
                        enabled: versionCount >= 2 && leftVersion.currentValue !== undefined && rightVersion.currentValue !== undefined
                        onClicked: compareData = bridge.compareVersions(leftVersion.currentValue, rightVersion.currentValue)
                    }
                }
                Label {
                    text: compareData.ok ? ("שינוי ציון: " + compareData.score_delta + " · תלמידים שעברו: " + compareData.moved_count) : (compareData.message || "")
                    font.pixelSize: 18
                    font.bold: true
                    color: compareData.score_delta >= 0 ? "#067647" : "#b42318"
                }
            }
            Panel {
                Layout.preferredHeight: 420
                SectionTitle { text: "תלמידים שעברו כיתה" }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: compareData.moved_students || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 42
                        color: "#ffffff"
                        border.color: "#e5e7eb"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Label { text: modelData.student_name; Layout.fillWidth: true }
                            Label { text: modelData.from_class + " → " + modelData.to_class; Layout.preferredWidth: 220; color: "#475467" }
                        }
                    }
                }
            }
        }
    }

    component ExportPage: PageShell {
        title: "ייצוא"
        subtitle: "ייצוא Excel כולל גיליון כללי, גיליון לכל כיתה, סטטיסטיקות, חריגות, תלמידים ללא חבר, שינויים ידניים והגדרות."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 220
                Button {
                    text: "ייצוא לקובץ Excel"
                    highlighted: true
                    enabled: hasAssignment()
                    Layout.preferredHeight: 46
                    onClicked: exportDialog.open()
                }
                Label {
                    text: "הקובץ נוצר מקומית בלבד. אין שליחה החוצה."
                    color: "#475467"
                }
            }
        }
    }

    component SettingsPage: PageShell {
        title: "הגדרות פרטיות"
        subtitle: "AI כבוי כברירת מחדל. כאן מנהלים שירותי AI ומציגים את סיכום הנתונים האנונימי לפני שימוש."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 620
                RowLayout {
                    Label { text: "AI"; Layout.preferredWidth: 120 }
                    Switch { id: aiSwitch; text: "מופעל"; checked: aiSettingsData.enabled || false }
                    Button {
                        text: "שמירת העדפה"
                        onClicked: {
                            bridge.saveAiPreferences(aiSwitch.checked, aiProvider.currentText)
                            aiSettingsData = bridge.aiSettings()
                        }
                    }
                }
                RowLayout {
                    Label { text: "שירות AI"; Layout.preferredWidth: 120 }
                    ComboBox { id: aiProvider; model: ["OpenAI", "Anthropic", "Gemini"]; enabled: aiSwitch.checked; Layout.fillWidth: true }
                }
                RowLayout {
                    Label { text: "מפתח API"; Layout.preferredWidth: 120 }
                    TextField { id: aiToken; echoMode: TextInput.Password; enabled: aiSwitch.checked; placeholderText: "sk-... / Anthropic / Gemini"; Layout.fillWidth: true }
                    Button {
                        text: "שמירה מקומית"
                        enabled: aiSwitch.checked
                        onClicked: {
                            settingsAiOutput = bridge.saveAiToken(aiProvider.currentText, aiToken.text)
                            aiSettingsData = bridge.aiSettings()
                        }
                    }
                    Button {
                        text: "בדיקת חיבור"
                        enabled: aiSwitch.checked && !settingsAiBusy
                        onClicked: {
                            settingsAiBusy = true
                            settingsAiOutput = "בודק חיבור AI..."
                            var result = bridge.testAiConnectionAsync(aiProvider.currentText)
                            if (!result.started) {
                                settingsAiBusy = false
                                settingsAiOutput = result.message || ""
                            }
                        }
                    }
                    Button {
                        text: "בדיקת כל שירותי ה-AI"
                        enabled: aiSwitch.checked && !settingsAiBusy
                        onClicked: {
                            settingsAiBusy = true
                            settingsAiOutput = "בודק את כל שירותי ה-AI..."
                            var result = bridge.testAllAiConnectionsAsync()
                            if (!result.started) {
                                settingsAiBusy = false
                                settingsAiOutput = result.message || ""
                            }
                        }
                    }
                    Button {
                        text: "איתור מודלים מתאימים"
                        enabled: aiSwitch.checked && !settingsAiBusy
                        onClicked: {
                            settingsAiBusy = true
                            settingsAiOutput = "בודק מודלים זמינים..."
                            var result = bridge.findSuitableAiModelsAsync()
                            if (!result.started) {
                                settingsAiBusy = false
                                settingsAiOutput = result.message || ""
                            }
                        }
                    }
                }
                Label {
                    text: "מפתח ה-AI נשמר מקומית במחשב הזה. אין צורך לדעת היכן הקובץ נמצא; מספיק לבחור ספק, להדביק מפתח וללחוץ שמירה."
                    color: "#475467"
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 92
                    radius: 8
                    color: "#eef4ff"
                    border.color: "#84caff"
                    Label {
                        anchors.fill: parent
                        anchors.margins: 10
                        text: "איך AI עובד כאן: השיבוץ עצמו תמיד מחושב מקומית. אם AI מופעל והשיבוץ קיבל ציון נמוך מסף הבדיקה, המערכת שולחת סיכום נתונים אנונימי ומצומצם לכל שירות AI שיש לו מפתח שמור, מקבלת תשובה מובנית, ובוחרת המלצה אחת להצגה. שום המלצה לא מיושמת אוטומטית בלי פעולה של המורה."
                        wrapMode: Text.WordWrap
                        color: "#1849a9"
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 62
                    radius: 8
                    color: "#fff7ed"
                    border.color: "#f79009"
                    Label {
                        anchors.fill: parent
                        anchors.margins: 10
                        text: "אזהרה לפני שליחה: אין לשלוח שמות תלמידים או הערות רגישות. המערכת שולחת רק נתונים אנונימיים, ורק אם AI מופעל והרשאת הפרויקט פעילה."
                        wrapMode: Text.WordWrap
                        color: "#7a2e0e"
                    }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 110
                    clip: true
                    model: aiSettingsData.providers || []
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 34
                        color: modelData.configured ? "#ecfdf3" : "#f8fafc"
                        border.color: modelData.configured ? "#079455" : "#dde5ef"
                        radius: 6
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            Label { text: modelData.provider; Layout.preferredWidth: 120; font.bold: true }
                            Label { text: "מודל: " + (modelData.model || "-"); Layout.preferredWidth: 240; color: "#475467"; elide: Text.ElideRight }
                            Label { text: modelData.configured ? "מפתח שמור" : "צריך להכניס מפתח"; Layout.preferredWidth: 150; color: modelData.configured ? "#067647" : "#b42318" }
                            Label { text: modelData.configured ? "מוכן לבדיקה" : "הדביקו מפתח ולחצו שמירה"; Layout.fillWidth: true; color: "#667085" }
                        }
                    }
                }
                Button {
                    text: "הצגת סיכום נתונים אנונימי"
                    enabled: hasProject()
                    onClicked: settingsAiOutput = bridge.anonymizedPayloadPreview()
                }
                RowLayout {
                    Layout.fillWidth: true
                    Button {
                        text: settingsAiBusy ? "בודק..." : "איתור בעיות בנתונים"
                        enabled: hasProject() && !settingsAiBusy
                        onClicked: {
                            settingsAiBusy = true
                            settingsAiOutput = "מכין תשובה..."
                            bridge.askAiAssistantAsync("settings", "אתר בעיות אפשריות בנתונים האנונימיים ובמדדי השיבוץ.", projectAiAllowed)
                        }
                    }
                    Button {
                        text: settingsAiBusy ? "בודק..." : "הצעת תיקונים"
                        enabled: hasProject() && !settingsAiBusy
                        onClicked: {
                            settingsAiBusy = true
                            settingsAiOutput = "מכין הצעות תיקון..."
                            bridge.askAiAssistantAsync("settings", "הצע תיקונים ידניים אפשריים בלי לקבל החלטת שיבוץ סופית.", projectAiAllowed)
                        }
                    }
                    Button {
                        text: settingsAiBusy ? "בודק..." : "דוח למנהלת"
                        enabled: hasProject() && !settingsAiBusy
                        onClicked: {
                            settingsAiBusy = true
                            settingsAiOutput = "מכין דוח למנהלת..."
                            bridge.askAiAssistantAsync("settings", "נסח דוח קצר למנהלת על איכות השיבוץ.", projectAiAllowed)
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                TextArea {
                    id: aiPayload
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    readOnly: true
                    wrapMode: TextEdit.NoWrap
                    font.family: "Consolas"
                    text: settingsAiOutput
                }
            }
        }
    }

    component HelpPage: PageShell {
        title: "עזרה"
        subtitle: "מדריך קצר להפעלה מלאה של Mosaicly."

        ColumnLayout {
            width: parent.width
            anchors.margins: 24
            spacing: 18
            Panel {
                Layout.preferredHeight: 620
                SectionTitle { text: "זרימת עבודה מומלצת" }
                Label { text: "1. צרו פרויקט והגדירו שמות כיתות."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "2. ייבאו CSV/XLSX ובחרו גיליון אם צריך."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "3. אשרו מיפוי עמודות או טענו תבנית מיפוי."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "4. עברו על בדיקת הנתונים ותקנו תלמידים עם בעיות."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "5. הגדירו כללי שיבוץ ושמרו תבנית כללים אם תרצו."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "6. הריצו שיבוץ, בדקו דוחות ואילוצים מתנגשים."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "7. בצעו העברה/החלפה/נעילה ידנית לפי הצורך."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "8. השוו גרסאות וייצאו Excel."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                SectionTitle { text: "פרטיות ו-AI" }
                Label {
                    text: "השיבוץ מבוצע תמיד בחישוב מקומי. AI הוא כלי עזר להסבר, סיכום והצעות בלבד. שמות תלמידים והערות רגישות לא נשלחים; במסך הגדרות אפשר לראות בדיוק את סיכום הנתונים האנונימי."
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    color: "#475467"
                }
                SectionTitle { text: "קבצים חשובים" }
                Label { text: "קובץ הרצה: RUN_CLASSBALANCER.bat"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "קובץ דוגמה: examples\\demo_students.csv"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Label { text: "הגדרת טוקנים: %USERPROFILE%\\.class_balancer\\.env או D:\\classmaker\\.env"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
        }
    }

    component PreviewTable: ScrollView {
        id: table
        property var rows: []
        property var headers: []
        clip: true
        Layout.fillWidth: true
        Layout.fillHeight: true
        contentWidth: Math.max(availableWidth, headers.length * 150)
        contentHeight: previewContent.implicitHeight
        ScrollBar.vertical.policy: ScrollBar.AsNeeded
        ScrollBar.horizontal.policy: ScrollBar.AsNeeded
        Column {
            id: previewContent
            width: table.contentWidth
            Row {
                Repeater {
                    model: headers
                    Rectangle {
                        width: 150
                        height: 38
                        color: "#eef4ff"
                        border.color: "#d0d5dd"
                        Label {
                            anchors.centerIn: parent
                            text: modelData
                            font.bold: true
                            elide: Text.ElideRight
                            width: parent.width - 12
                        }
                    }
                }
            }
            Repeater {
                model: rows
                Row {
                    property var rowData: modelData
                    Repeater {
                        model: headers
                        Rectangle {
                            width: 150
                            height: 34
                            color: "#ffffff"
                            border.color: "#eaecf0"
                            Label {
                                anchors.centerIn: parent
                                width: parent.width - 12
                                text: rowData ? rowData[modelData] : ""
                                elide: Text.ElideRight
                                color: "#172033"
                            }
                        }
                    }
                }
            }
        }
    }

    component RuleSwitch: Rectangle {
        id: ruleSwitchRoot
        property string title: ""
        property string help: ""
        property alias checked: switchControl.checked
        Layout.fillWidth: true
        implicitHeight: help.length > 0 ? 72 : 46
        radius: 8
        color: "#f8fafc"
        border.color: "#e5e7eb"
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 3
            RowLayout {
                Layout.fillWidth: true
                Label { text: ruleSwitchRoot.title; Layout.fillWidth: true; font.pixelSize: 16; font.bold: true; color: "#172033"; wrapMode: Text.WordWrap }
                Switch { id: switchControl; enabled: ruleSwitchRoot.enabled }
            }
            Label {
                visible: ruleSwitchRoot.help.length > 0
                text: ruleSwitchRoot.help
                color: "#667085"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }

    component RuleWeight: Rectangle {
        id: ruleWeightRoot
        property string title: ""
        property string help: ""
        property real value: 1
        Layout.fillWidth: true
        implicitHeight: 84
        radius: 8
        color: "#ffffff"
        border.color: "#e5e7eb"
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 3
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: ruleWeightRoot.title
                    Layout.preferredWidth: 120
                    font.bold: true
                    color: "#172033"
                    elide: Text.ElideRight
                }
                Slider {
                    id: weightSlider
                    from: 0
                    to: 3
                    stepSize: 0.1
                    value: ruleWeightRoot.value
                    Layout.fillWidth: true
                    onMoved: ruleWeightRoot.value = value
                }
                Label {
                    text: Number(ruleWeightRoot.value).toFixed(1)
                    Layout.preferredWidth: 42
                    horizontalAlignment: Text.AlignHCenter
                    color: "#475467"
                }
            }
            Label {
                text: ruleWeightRoot.help
                color: "#667085"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }

    component HelpButton: Button {
        property string helpText: ""
        text: "?"
        flat: true
        Layout.preferredWidth: 28
        Layout.preferredHeight: 28
        ToolTip.visible: hovered
        ToolTip.delay: 250
        ToolTip.text: helpText
    }

    component TableHeader: Label {
        property string textValue: ""
        property int cellWidth: 80
        width: cellWidth
        height: 22
        text: textValue
        font.bold: true
        color: "#172033"
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    component TableCell: Label {
        property string textValue: ""
        property int cellWidth: 80
        property bool muted: false
        property bool bold: false
        property color accent: muted ? "#667085" : "#172033"
        width: cellWidth
        height: 32
        text: textValue
        color: accent
        font.bold: bold
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    component FriendSlots: Row {
        property var slots: []
        property int cellWidth: 180
        width: cellWidth
        height: 32
        spacing: 6
        Repeater {
            model: [1, 2, 3]
            Rectangle {
                property var slot: (slots && slots.length >= modelData) ? slots[modelData - 1] : ({ priority: modelData, requested: false, received: false, name: "" })
                width: 42
                height: 28
                radius: 6
                color: friendSlotColor(slot)
                border.color: Qt.darker(color, 1.06)
                Label {
                    anchors.centerIn: parent
                    text: String(modelData)
                    color: friendSlotTextColor(slot)
                    font.bold: true
                }
                ToolTip.visible: slotMouse.containsMouse
                ToolTip.delay: 250
                ToolTip.text: !slot.requested ? ("חבר " + modelData + ": לא הוגדר") : (slot.received ? ("חבר " + modelData + " התקבל: " + slot.name) : ("חבר " + modelData + " לא התקבל: " + slot.name))
                MouseArea {
                    id: slotMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.NoButton
                }
            }
        }
    }

    function severityColor(severity, alpha) {
        if (severity === "critical") return alpha < 1 ? "#fee4e2" : "#d92d20"
        if (severity === "warning") return alpha < 1 ? "#fff4e5" : "#f79009"
        return alpha < 1 ? "#eef4ff" : "#2563eb"
    }
}
