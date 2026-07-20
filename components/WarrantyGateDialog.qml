import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

// Gate 1 (issue #47): warranty warning shown at launch, blocking the whole UI
// until the user explicitly accepts or declines. Controls follow the ticket
// mockup: mutually-exclusive Accept / Decline checkboxes plus an OK button
// that stays disabled until exactly one is picked.
Dialog {
    id: control

    // Named with the `Warranty` suffix to avoid clashing with Dialog's own
    // built-in `accepted` / `rejected` signals.
    signal acceptedWarranty()
    signal declinedWarranty()

    title: "Warning"
    width: 560
    modal: true

    // Cannot be dismissed by Esc or by clicking outside — the user must choose.
    closePolicy: Popup.NoAutoClose

    // Parent to the window-wide overlay: main.qml is a frameless
    // ApplicationWindow, so this is what centres the dialog on the window.
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
        color: "#E0E0E0"
        font.pixelSize: 16
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    contentItem: ColumnLayout {
        spacing: 18

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Warning: Unofficial changes to the device will void your " +
                  "warranty. Proceed at Own Risk."
            color: "#E67E22"
            font.pixelSize: 14
            font.bold: true
        }

        RowLayout {
            spacing: 24

            CheckBox {
                id: acceptBox
                text: "Accept"
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    font.pixelSize: 13
                    leftPadding: parent.indicator.width + parent.spacing
                    verticalAlignment: Text.AlignVCenter
                }
                // Mutually exclusive with declineBox. The `checked` guard stops
                // the two handlers bouncing off each other.
                onCheckedChanged: if (checked && declineBox.checked) declineBox.checked = false
            }

            CheckBox {
                id: declineBox
                text: "Decline"
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    font.pixelSize: 13
                    leftPadding: parent.indicator.width + parent.spacing
                    verticalAlignment: Text.AlignVCenter
                }
                onCheckedChanged: if (checked && acceptBox.checked) acceptBox.checked = false
            }

            Item { Layout.fillWidth: true }
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10

            Button {
                id: okButton
                text: "OK"
                Layout.preferredWidth: 120
                Layout.preferredHeight: 32
                // Exactly one box must be checked.
                enabled: acceptBox.checked !== declineBox.checked
                background: Rectangle {
                    color: okButton.enabled ? (okButton.hovered ? "#E67E22" : "#3A3F4B") : "#2C2F36"
                    radius: 4
                    border.color: okButton.enabled ? "#E67E22" : "#555"
                }
                contentItem: Text {
                    text: okButton.text
                    color: okButton.enabled ? "#E67E22" : "#7F8C8D"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.bold: true
                }
                onClicked: {
                    if (acceptBox.checked) {
                        control.close()
                        control.acceptedWarranty()
                    } else {
                        // Leave the dialog up; the app is about to quit.
                        control.declinedWarranty()
                    }
                }
            }
        }
    }
}
