import QtQuick 6.0
import QtQuick.Controls 6.0

Rectangle {
    width: 120
    height: 120
    color: "transparent"

    // === Properties for IMU data ===
    property string imuLabel: "IMU Data"
    property string mode: "Accel"     // or "Gyro"
    property int xVal: 0
    property int yVal: 0
    property int zVal: 0

    // === Border Circle ===
    Rectangle {
        width: 105
        height: 105
        radius: 52.5
        anchors.centerIn: parent
        border.color: "#D0D3D4"
        border.width: 3
        color: "transparent"
    }

    // === IMU Values ===
    Column {
        anchors.centerIn: parent
        spacing: 4

        Text {
            text: mode
            font.pixelSize: 12
            font.bold: true
            color: "#2C3E50"
            horizontalAlignment: Text.AlignHCenter
            anchors.horizontalCenter: parent.horizontalCenter
        }

        Text {
            text: "X: " + xVal
            font.pixelSize: 11
            color: "#3498DB"
            anchors.horizontalCenter: parent.horizontalCenter
        }

        Text {
            text: "Y: " + yVal
            font.pixelSize: 11
            color: "#27AE60"
            anchors.horizontalCenter: parent.horizontalCenter
        }

        Text {
            text: "Z: " + zVal
            font.pixelSize: 11
            color: "#E67E22"
            anchors.horizontalCenter: parent.horizontalCenter
        }
    }

    // === Label Below Widget ===
    Text {
        text: imuLabel
        anchors {
            top: parent.bottom
            horizontalCenter: parent.horizontalCenter
            topMargin: 5
        }
        font.pixelSize: 16
        color: "#BDC3C7"
        font.weight: Font.Medium
    }
}
