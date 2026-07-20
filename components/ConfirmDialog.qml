import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

// Reusable two-button confirmation, styled to match the hand-rolled dialogs
// elsewhere in the app (see the odometer reset in pages/Console.qml).
//
//   ConfirmDialog {
//       id: saveConfirm
//       title: "Confirm Configuration Change"
//       warningText: "Warning: ..."
//       onConfirmed: doTheThing()
//   }
//   ... saveConfirm.open()
Dialog {
    id: control

    // Bold orange lead paragraph.
    property string warningText: ""
    // Plain follow-up question below it.
    property string questionText: "Are you sure you want to proceed?"
    property string declineText: "No"
    property string confirmText: "Yes"

    // Emitted when the user picks the confirm button. Named `confirmed` rather
    // than `accepted` because Dialog already defines an `accepted` signal.
    signal confirmed()

    width: 520
    modal: true

    // Parent to the window-wide overlay so the dialog centres on the whole
    // window. main.qml is a frameless ApplicationWindow and pages are inset,
    // so centring on the enclosing page item would be off-centre.
    parent: Overlay.overlay
    anchors.centerIn: parent

    padding: 20

    background: Rectangle {
        color: "#2C2F36"
        radius: 8
        border.color: "#3A3F4B"
    }

    header: Label {
        text: control.title
        visible: control.title.length > 0
        color: "#E0E0E0"
        font.pixelSize: 16
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    contentItem: ColumnLayout {
        spacing: 16

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: control.warningText
            visible: text.length > 0
            color: "#E67E22"
            font.pixelSize: 14
            font.bold: true
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: control.questionText
            visible: text.length > 0
            color: "#BDC3C7"
            font.pixelSize: 13
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: 4
            spacing: 10

            Button {
                text: control.declineText
                Layout.preferredWidth: 100
                Layout.preferredHeight: 32
                background: Rectangle {
                    color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                    radius: 4
                    border.color: "#BDC3C7"
                }
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: control.close()
            }

            Button {
                text: control.confirmText
                Layout.preferredWidth: 120
                Layout.preferredHeight: 32
                background: Rectangle {
                    color: parent.hovered ? "#E67E22" : "#3A3F4B"
                    radius: 4
                    border.color: "#E67E22"
                }
                contentItem: Text {
                    text: parent.text
                    color: "#E67E22"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.bold: true
                }
                onClicked: {
                    control.close()
                    control.confirmed()
                }
            }
        }
    }
}
