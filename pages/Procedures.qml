import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Controls.Material 6.0
import QtQuick.Layouts 6.0
import OpenMotion 1.0

Rectangle {
    id: proceduresPage
    color: "#1C1C1E"
    radius: 10

    // The page is re-instantiated whenever the sidebar selects it, so this
    // fires exactly when the operator opens the pane (audit logging).
    Component.onCompleted: ProceduresController.paneOpened()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        // ------------------------------------------------ header controls
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            ComboBox {
                id: procedureCombo
                Layout.preferredWidth: 420
                model: ProceduresController.procedureNames
                enabled: !ProceduresController.running
                onActivated: ProceduresController.selectProcedure(currentIndex)
            }

            Button {
                id: startStopButton
                text: ProceduresController.running ? "Stop" : "Start"
                Material.background: ProceduresController.running
                    ? "#C0392B" : "#27AE60"
                Material.foreground: "white"
                onClicked: {
                    if (ProceduresController.running)
                        ProceduresController.stopProcedure()
                    else
                        ProceduresController.startProcedure(
                            procedureCombo.currentIndex)
                }
            }

            // ---------------------------------------------- status pill
            Rectangle {
                id: statusPill
                Layout.preferredHeight: 34
                Layout.preferredWidth: statusRow.implicitWidth + 26
                radius: 17
                color: "#2C2C2E"
                border.width: 1
                border.color: statusDot.color

                RowLayout {
                    id: statusRow
                    anchors.centerIn: parent
                    spacing: 8

                    Rectangle {
                        id: statusDot
                        width: 12
                        height: 12
                        radius: 6
                        color: ProceduresController.status === "running"
                                   ? "#F1C40F"
                               : ProceduresController.status === "pass"
                                   ? "#27AE60"
                               : ProceduresController.status === "fail"
                                   ? "#C0392B"
                                   : "#7F8C8D"

                        SequentialAnimation on opacity {
                            running: ProceduresController.status === "running"
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.3; duration: 600 }
                            NumberAnimation { to: 1.0; duration: 600 }
                            onRunningChanged: if (!running) statusDot.opacity = 1.0
                        }
                    }

                    Label {
                        text: ProceduresController.status === "running"
                                  ? "In process"
                              : ProceduresController.status === "pass"
                                  ? "Pass"
                              : ProceduresController.status === "fail"
                                  ? "Fail"
                                  : "Idle"
                        color: "white"
                        font.pixelSize: 14
                    }
                }
            }

            CheckBox {
                id: verboseCheck
                text: "Verbose"
                checked: ProceduresController.verbose
                onToggled: ProceduresController.verbose = checked
                Material.accent: "#27AE60"
            }

            Item { Layout.fillWidth: true }

            Label {
                visible: ProceduresController.promptType === "text"
                text: "Operator input needed"
                color: "#F1C40F"
                font.pixelSize: 14
            }
        }

        // ------------------------------------------------------- terminal
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#0D0D0F"
            radius: 8
            border.color: "#3A3A3C"
            border.width: 1

            ScrollView {
                id: termScroll
                anchors.fill: parent
                anchors.margins: 8
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                TextArea {
                    id: terminal
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    persistentSelection: true
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    color: "#D5D8DC"
                    font.family: "Consolas"
                    font.pixelSize: 16
                    background: null

                    // Restore the log when the page is (re)created - the
                    // Loader destroys pages on tab switch, the controller
                    // keeps the authoritative buffer. visibleLog respects
                    // the Verbose checkbox.
                    Component.onCompleted: {
                        text = ProceduresController.visibleLog
                        cursorPosition = length
                    }
                }
            }

            Connections {
                target: ProceduresController
                function onLogLine(line) {
                    terminal.append(line)
                    // Auto-scroll only if the user isn't scrolled back
                    // reviewing earlier output.
                    if (termScroll.ScrollBar.vertical.position
                            + termScroll.ScrollBar.vertical.size > 0.95)
                        terminal.cursorPosition = terminal.length
                }
                function onLogCleared() {
                    terminal.clear()
                }
                function onVerboseChanged() {
                    // Re-render the whole terminal under the new filter so
                    // the toggle applies retroactively, not just to new lines.
                    terminal.text = ProceduresController.visibleLog
                    terminal.cursorPosition = terminal.length
                }
                function onPromptChanged() {
                    if (ProceduresController.promptType === "text")
                        operatorInput.forceActiveFocus()
                }
            }
        }

        // ------------------------------------------------- operator input
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            // One button per option when the prompt ends with a small
            // option group like "(left/right)" or "[y/N]"; clicking sends
            // that option verbatim.
            Repeater {
                model: ProceduresController.promptOptions
                Button {
                    text: modelData
                    Material.background: "#F1C40F"
                    Material.foreground: "black"
                    onClicked: ProceduresController.answerPrompt(modelData)
                }
            }

            TextField {
                id: operatorInput
                Layout.fillWidth: true
                enabled: ProceduresController.running
                placeholderText: ProceduresController.running
                    ? "Type a response and press Enter"
                    : "Procedure input (available while running)"
                color: "#D5D8DC"
                font.family: "Consolas"
                font.pixelSize: 16
                Material.accent: ProceduresController.promptType === "text"
                    ? "#F1C40F" : "#27AE60"
                onAccepted: {
                    ProceduresController.answerPrompt(text)
                    clear()
                }
            }

            Button {
                text: "Send"
                enabled: ProceduresController.running
                Material.background: ProceduresController.promptType === "text"
                    ? "#F1C40F" : "#2C2C2E"
                Material.foreground: ProceduresController.promptType === "text"
                    ? "black" : "white"
                onClicked: {
                    ProceduresController.answerPrompt(operatorInput.text)
                    operatorInput.clear()
                }
            }
        }
    }
}
