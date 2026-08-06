import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Controls.Material 6.0
import QtQuick.Layouts 6.0
import OpenMotion 1.0

Rectangle {
    id: proceduresPage
    color: "#1C1C1E"
    radius: 10

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

            Item { Layout.fillWidth: true }

            // ------------------------------------- operator prompt answers
            Label {
                visible: ProceduresController.promptType !== ""
                text: "Operator input needed:"
                color: "#F1C40F"
                font.pixelSize: 14
            }

            Button {
                visible: ProceduresController.promptType === "continue"
                text: "Continue"
                Material.background: "#F1C40F"
                Material.foreground: "black"
                onClicked: ProceduresController.answerPrompt("y")
            }

            Button {
                visible: ProceduresController.promptType === "side"
                text: "Left"
                Material.background: "#F1C40F"
                Material.foreground: "black"
                onClicked: ProceduresController.answerPrompt("left")
            }

            Button {
                visible: ProceduresController.promptType === "side"
                text: "Right"
                Material.background: "#F1C40F"
                Material.foreground: "black"
                onClicked: ProceduresController.answerPrompt("right")
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
                ScrollBar.horizontal.policy: ScrollBar.AsNeeded

                TextArea {
                    id: terminal
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    persistentSelection: true
                    wrapMode: TextEdit.NoWrap
                    textFormat: TextEdit.PlainText
                    color: "#D5D8DC"
                    font.family: "Consolas"
                    font.pixelSize: 13
                    background: null

                    // Restore the full log when the page is (re)created -
                    // the Loader destroys pages on tab switch, the
                    // controller keeps the authoritative buffer.
                    Component.onCompleted: {
                        text = ProceduresController.fullLog
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
            }
        }
    }
}
