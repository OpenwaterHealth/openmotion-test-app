// components/ActionButton.qml
import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

Button {
    id: root

    // ——— Public API ———
    // Use `text` directly to set the label (standard Button property).
    // Bind `enabled` from the outside as usual, e.g. enabled: MotionInterface.consoleConnected
    signal triggered()
    property int cornerRadius: 4
    property color textColorEnabled:  "#BDC3C7"
    property color textColorDisabled: "#7F8C8D"
    property color bgColor:           "#3A3F4B"
    property color bgHoverColor:      "#4A90E2"
    property color borderColor:       "#BDC3C7"
    property color borderHoverColor:  "#FFFFFF"
    property color borderDisabled:    "#7F8C8D"

    // sensible defaults; can still be overridden by parent Layouts
    Layout.preferredWidth: 100
    Layout.preferredHeight: 40
    hoverEnabled: true

    contentItem: Text {
        text: root.text
        color: root.enabled ? root.textColorEnabled : root.textColorDisabled
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        anchors.fill: parent
        // small padding to keep text off the border if resized
        anchors.margins: 6
    }

    background: Rectangle {
        implicitWidth: 100
        implicitHeight: 40
        radius: root.cornerRadius
        color: {
            if (!root.enabled)           return root.bgColor
            return root.hovered ? root.bgHoverColor : root.bgColor
        }
        border.color: {
            if (!root.enabled)           return root.borderDisabled
            return root.hovered ? root.borderHoverColor : root.borderColor
        }
    }

    onClicked: root.triggered()
}
