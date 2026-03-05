import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Dialogs 6.2
import OpenMotion 1.0

Rectangle {
    id: page1
    width: parent.width
    height: parent.height
    color: "#29292B" // Background color for Page 1
    radius: 20
    opacity: 0.95 // Slight transparency for the content area

    // Minimal device/app info for the Settings overview page
    property string consoleFirmwareVersion: "N/A"
    property string consoleDeviceId: "N/A"
    property string consoleBoardRevId: "N/A"

    // Latest firmware info from remote
    property string consoleLatestFirmware: "N/A"
    property string consoleLatestFirmwareDate: ""
    property var consoleReleasesModel: []
    property int consoleLatestIndex: 0

    // Left sensor latest firmware info
    property string leftLatestFirmware: "N/A"
    property string leftLatestFirmwareDate: ""
    property var leftReleasesModel: []
    property int leftLatestIndex: 0

    // Right sensor latest firmware info
    property string rightLatestFirmware: "N/A"
    property string rightLatestFirmwareDate: ""
    property var rightReleasesModel: []
    property int rightLatestIndex: 0

    property string leftSensorFirmwareVersion: "N/A"
    property string leftSensorDeviceId: "N/A"
    property string rightSensorFirmwareVersion: "N/A"
    property string rightSensorDeviceId: "N/A"

    // Console firmware update UI state
    property string consoleFwToken: ""
    property string consoleFwSelectedTag: ""
    property string consoleFwFilename: ""
    property string consoleFwStageText: ""
    property int consoleFwPercent: -1
    property string consoleFwMessage: ""
    // Current firmware update target (CONSOLE, SENSOR_LEFT, SENSOR_RIGHT)
    property string fwUpdateTarget: "CONSOLE"
    // Target used when opening the upload dialog
    property string fwUploadTarget: "CONSOLE"

    // User configuration values (editable by user)
    property real userTecTrip: 0.00
    property real userOptGain: 0.00
    property real userOptThresh: 0.00
    property real userEEGain: 0.00
    property real userEEThresh: 0.00

    // Busy state while reading user config from device
    property bool userConfigLoading: false

    // Modal dialog styling (firmware update)
    property int modalMaxWidth: 520
    property int modalMinWidth: 420
    property int modalPadding: 18
    property color modalOverlayColor: "#B0000000"   // darker than default to avoid washed-out background
    property color modalBackgroundColor: "#1E1E20"
    property color modalBorderColor: "#3E4E6F"
    property int modalBorderWidth: 2
    property int modalRadius: 12

    function modalWidthFor(parentItem) {
        var w = modalMinWidth
        if (parentItem && parentItem.width)
            w = Math.min(modalMaxWidth, Math.max(modalMinWidth, parentItem.width * 0.85))
        return Math.round(w)
    }

    function refreshConsoleInfo() {
        if (MOTIONInterface.consoleConnected) {
            MOTIONInterface.queryConsoleInfo()
            MOTIONInterface.queryConsoleLatestVersionInfo()
        }
    }

    function refreshSensorInfo(target) {
        if (target === "SENSOR_LEFT" && MOTIONInterface.leftSensorConnected) {
            MOTIONInterface.querySensorInfo(target)
            MOTIONInterface.querySensorLatestVersionInfo(target)
        }
        if (target === "SENSOR_RIGHT" && MOTIONInterface.rightSensorConnected) {
            MOTIONInterface.querySensorInfo(target)
            MOTIONInterface.querySensorLatestVersionInfo(target)
        }
    }

    Connections {
        target: MOTIONInterface

        function _clearConsoleInfo() {
            consoleFirmwareVersion = "N/A"
            consoleDeviceId = "N/A"
            consoleBoardRevId = "N/A"
        }

        function _clearLeftSensorInfo() {
            leftSensorFirmwareVersion = "N/A"
            leftSensorDeviceId = "N/A"
        }

        function _clearRightSensorInfo() {
            rightSensorFirmwareVersion = "N/A"
            rightSensorDeviceId = "N/A"
        }

        // Mirrors Sensor.qml/Console.qml behavior: on any connection change, clear disconnected
        // fields immediately and query device info for connected modules.
        function onConnectionStatusChanged() {
            if (!MOTIONInterface.consoleConnected) {
                _clearConsoleInfo()
                userConfigLoading = false
            }
            if (!MOTIONInterface.leftSensorConnected)
                _clearLeftSensorInfo()
            if (!MOTIONInterface.rightSensorConnected)
                _clearRightSensorInfo()

            if (MOTIONInterface.consoleConnected || MOTIONInterface.leftSensorConnected || MOTIONInterface.rightSensorConnected) {
                if (MOTIONInterface.consoleConnected)
                    userConfigLoading = true
                settingsInfoTimer.restart()
                if (MOTIONInterface.consoleConnected)
                    MOTIONInterface.queryConsoleLatestVersionInfo()
            } else {
                settingsInfoTimer.stop()
            }
        }

        function onConsoleDeviceInfoReceived(fwVersion, devId, boardId) {
            consoleFirmwareVersion = fwVersion
            consoleDeviceId = devId
            consoleBoardRevId = boardId
        }

        function onLatestVersionInfoReceived(info) {
            if (!info) return
            // Expecting structure: { latest: { tag_name, published_at }, releases: { tag: { published_at, prerelease } }}
            try {
                if (info.latest && info.latest.tag_name) {
                    consoleLatestFirmware = info.latest.tag_name
                    consoleLatestFirmwareDate = info.latest.published_at || ""
                } else {
                    consoleLatestFirmware = "N/A"
                    consoleLatestFirmwareDate = ""
                }

                var names = []
                for (var k in info.releases) {
                    names.push(k)
                }
                // Sort by published_at descending
                names.sort(function(a,b){
                    var da = new Date(info.releases[a].published_at).getTime()
                    var db = new Date(info.releases[b].published_at).getTime()
                    return db - da
                })
                consoleReleasesModel = names
                var idx = consoleReleasesModel.indexOf(consoleLatestFirmware)
                consoleLatestIndex = idx >= 0 ? idx : 0
            } catch (e) {
                console.log('Error parsing latest version info', e)
            }
        }

        function onLatestSensorVersionInfoReceived(target, info) {
            if (!info) return
            try {
                var names = []
                for (var k in info.releases) {
                    names.push(k)
                }
                names.sort(function(a,b){
                    var da = new Date(info.releases[a].published_at).getTime()
                    var db = new Date(info.releases[b].published_at).getTime()
                    return db - da
                })

                if (target === "SENSOR_LEFT") {
                    if (info.latest && info.latest.tag_name) {
                        leftLatestFirmware = info.latest.tag_name
                        leftLatestFirmwareDate = info.latest.published_at || ""
                    } else {
                        leftLatestFirmware = "N/A"
                        leftLatestFirmwareDate = ""
                    }
                    leftReleasesModel = names
                    var idxL = leftReleasesModel.indexOf(leftLatestFirmware)
                    leftLatestIndex = idxL >= 0 ? idxL : 0
                } else if (target === "SENSOR_RIGHT") {
                    if (info.latest && info.latest.tag_name) {
                        rightLatestFirmware = info.latest.tag_name
                        rightLatestFirmwareDate = info.latest.published_at || ""
                    } else {
                        rightLatestFirmware = "N/A"
                        rightLatestFirmwareDate = ""
                    }
                    rightReleasesModel = names
                    var idxR = rightReleasesModel.indexOf(rightLatestFirmware)
                    rightLatestIndex = idxR >= 0 ? idxR : 0
                }
            } catch (e) {
                console.log('Error parsing sensor latest version info', e)
            }
        }

        // Newer signal (preferred): includes target so Settings can show both L/R.
        function onSensorDeviceInfoReceivedEx(target, fwVersion, devId) {
            if (target === "SENSOR_LEFT") {
                leftSensorFirmwareVersion = fwVersion
                leftSensorDeviceId = devId
            } else if (target === "SENSOR_RIGHT") {
                rightSensorFirmwareVersion = fwVersion
                rightSensorDeviceId = devId
            }
        }

        function onConsoleFirmwareUpdateProgress(target, stage, percent, message) {
            fwUpdateTarget = target
            if (stage === "download")
                consoleFwStageText = "Downloading firmware"
            else if (stage === "flash")
                consoleFwStageText = "Updating firmware"
            else
                consoleFwStageText = "Working"

            consoleFwPercent = percent
            consoleFwMessage = message
            if (!fwProgressDialog.opened)
                fwProgressDialog.open()
        }

        function onConsoleFirmwareDownloadReady(token, tag, filename, target) {
            fwUpdateTarget = target
            consoleFwToken = token
            consoleFwSelectedTag = tag
            consoleFwFilename = filename
            fwProgressDialog.close()
            fwConfirmDialog.open()
        }

        function onConsoleFirmwareUpdateFinished(target, success, message) {
            fwUpdateTarget = target
            fwProgressDialog.close()
            consoleFwToken = ""
            fwResultDialog.title = success ? "Firmware Update Complete" : "Firmware Update Failed"
            var prefix = (target === "CONSOLE") ? "Console: " : (target === "SENSOR_LEFT") ? "Left sensor: " : "Right sensor: "
            fwResultDialog.message = prefix + message
            fwResultDialog.open()
        }

        function onConsoleFirmwareUpdateError(target, message) {
            fwUpdateTarget = target
            fwProgressDialog.close()
            fwErrorDialog.message = message
            fwErrorDialog.open()
            consoleFwToken = ""
        }

        function onUserConfigLoaded(tecTrip, optGain, optThresh, eeGain, eeThresh) {
            userConfigLoading = false
            userTecTrip   = tecTrip
            userOptGain   = optGain
            userOptThresh = optThresh
            userEEGain    = eeGain
            userEEThresh  = eeThresh
            tecTripField.text   = tecTrip.toFixed(2)
            optGainField.text   = optGain.toFixed(2)
            optThreshField.text = optThresh.toFixed(2)
            eeGainField.text    = eeGain.toFixed(2)
            eeThreshField.text  = eeThresh.toFixed(2)
        }

        function onUserConfigError(message) {
            userConfigLoading = false
        }
    }

    // Small delay after connect to let the device stabilize (matches pattern in other pages)
    Timer {
        id: settingsInfoTimer
        interval: 1500
        repeat: false
        onTriggered: {
            refreshConsoleInfo()
            refreshSensorInfo("SENSOR_LEFT")
            refreshSensorInfo("SENSOR_RIGHT")
            if (MOTIONInterface.consoleConnected)
                MOTIONInterface.readUserConfig()
        }
    }

    Component.onCompleted: {
        // Populate immediately if user navigates here while already connected
        if (MOTIONInterface.consoleConnected || MOTIONInterface.leftSensorConnected || MOTIONInterface.rightSensorConnected) {
            if (MOTIONInterface.consoleConnected)
                userConfigLoading = true
            settingsInfoTimer.start()
        }
    }

    Dialog {
        id: fwErrorDialog
        parent: contentArea
        modal: true
        title: "Firmware Update"
        standardButtons: Dialog.NoButton
        property string message: ""
        closePolicy: Popup.NoAutoClose
        footer: null

        width: modalWidthFor(parent)
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        padding: modalPadding

        Overlay.modal: Rectangle { color: modalOverlayColor }

        header: Item {
            implicitHeight: 46

            Rectangle {
                id: fwErrorHeaderBg
                anchors.fill: parent
                anchors.margins: modalBorderWidth
                color: "#3A3F4B"
                radius: modalRadius
            }

            // Square off the bottom edge so only the top corners are rounded
            Rectangle {
                anchors.left: fwErrorHeaderBg.left
                anchors.right: fwErrorHeaderBg.right
                anchors.bottom: fwErrorHeaderBg.bottom
                height: modalRadius
                color: fwErrorHeaderBg.color
            }

            Text {
                text: fwErrorDialog.title
                anchors.left: parent.left
                anchors.leftMargin: modalPadding
                anchors.verticalCenter: parent.verticalCenter
                color: "white"
                font.pixelSize: 18
                font.weight: Font.Medium
                horizontalAlignment: Text.AlignLeft
                verticalAlignment: Text.AlignVCenter
            }
        }

        background: Rectangle {
            color: modalBackgroundColor
            radius: modalRadius
            border.color: modalBorderColor
            border.width: modalBorderWidth
        }

        contentItem: ColumnLayout {
            spacing: 12
            width: fwErrorDialog.width - fwErrorDialog.leftPadding - fwErrorDialog.rightPadding

            Text {
                text: fwErrorDialog.message
                color: "white"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Item { Layout.fillWidth: true }

                Button {
                    text: "OK"
                    Layout.preferredWidth: 110
                    Layout.preferredHeight: 40
                    hoverEnabled: true

                    onClicked: fwErrorDialog.close()

                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    background: Rectangle {
                        color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                        border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                        radius: 6
                    }
                }
            }
        }
    }

    FileDialog {
        id: fwUploadDialog
        title: "Select firmware file"
        nameFilters: ["Firmware binaries (*.bin)"]
        onAccepted: {
            var file = ""
            // Try several possible properties depending on Qt version
            if (typeof selectedFiles !== 'undefined' && selectedFiles && selectedFiles.length > 0) file = selectedFiles[0]
            else if (typeof fileUrls !== 'undefined' && fileUrls && fileUrls.length > 0) file = fileUrls[0]
            else if (typeof fileUrl !== 'undefined' && fileUrl) file = fileUrl
            else if (typeof file !== 'undefined' && file) file = file
            if (!file) return

            // Ensure we have a string (selectedFiles/fileUrls may yield QUrl or other object)
            if (file && typeof file !== 'string') {
                if (typeof file.toLocalFile === 'function') {
                    file = file.toLocalFile()
                } else if (typeof file.toString === 'function') {
                    file = file.toString()
                } else {
                    file = String(file)
                }
            }

            // If it's a file:// URL, strip the scheme
            if (typeof file === 'string' && file.indexOf("file://") === 0) {
                // Remove file:// or file:/// prefix
                file = file.replace(/^file:\/\//, "")
                // On Windows paths may start with /C:/, remove leading slash
                if (file.length > 0 && file[0] === '/' && file[2] === ':') file = file.substring(1)
            }

            // Normalize filename
            var idx = file.lastIndexOf("/")
            if (idx < 0) idx = file.lastIndexOf("\\\\")
            var fname = idx >= 0 ? file.substring(idx + 1) : file

            if (fwUploadTarget === "CONSOLE" && fname !== "motion-console-fw.bin") {
                fwErrorDialog.message = "Filename must be motion-console-fw.bin"
                fwErrorDialog.open()
                return
            }
            if ((fwUploadTarget === "SENSOR_LEFT" || fwUploadTarget === "SENSOR_RIGHT") && fname !== "motion-sensor-fw.bin") {
                fwErrorDialog.message = "Filename must be motion-sensor-fw.bin"
                fwErrorDialog.open()
                return
            }

            // Pass the local path to the connector (QML provides native path in selectedFiles/fileUrls)
            // No download step for local files — beginDeviceFirmwareFromLocal emits
            // consoleFirmwareDownloadReady synchronously, which opens fwConfirmDialog directly.
            consoleFwPercent = -1
            consoleFwMessage = ""
            consoleFwStageText = ""
            MOTIONInterface.beginDeviceFirmwareFromLocal(fwUploadTarget, file)
        }
    }

    Dialog {
        id: fwResultDialog
        parent: contentArea
        modal: true
        title: "Firmware Update"
        standardButtons: Dialog.NoButton
        property string message: ""
        closePolicy: Popup.NoAutoClose
        footer: null

        width: modalWidthFor(parent)
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        padding: modalPadding

        Overlay.modal: Rectangle { color: modalOverlayColor }

        header: Item {
            implicitHeight: 46

            Rectangle {
                id: fwResultHeaderBg
                anchors.fill: parent
                anchors.margins: modalBorderWidth
                color: "#3A3F4B"
                radius: modalRadius
            }

            Rectangle {
                anchors.left: fwResultHeaderBg.left
                anchors.right: fwResultHeaderBg.right
                anchors.bottom: fwResultHeaderBg.bottom
                height: modalRadius
                color: fwResultHeaderBg.color
            }

            Text {
                text: fwResultDialog.title
                anchors.left: parent.left
                anchors.leftMargin: modalPadding
                anchors.verticalCenter: parent.verticalCenter
                color: "white"
                font.pixelSize: 18
                font.weight: Font.Medium
                horizontalAlignment: Text.AlignLeft
                verticalAlignment: Text.AlignVCenter
            }
        }

        background: Rectangle {
            color: modalBackgroundColor
            radius: modalRadius
            border.color: modalBorderColor
            border.width: modalBorderWidth
        }

        contentItem: ColumnLayout {
            spacing: 12
            width: fwResultDialog.width - fwResultDialog.leftPadding - fwResultDialog.rightPadding

            Text {
                text: fwResultDialog.message
                color: "white"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Item { Layout.fillWidth: true }

                Button {
                    text: "OK"
                    Layout.preferredWidth: 110
                    Layout.preferredHeight: 40
                    hoverEnabled: true

                    onClicked: fwResultDialog.close()

                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    background: Rectangle {
                        color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                        border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                        radius: 6
                    }
                }
            }
        }
    }

    Dialog {
        id: fwConfirmDialog
        parent: contentArea
        modal: true
        title: "Confirm Firmware Update"
        standardButtons: Dialog.NoButton
        closePolicy: Popup.NoAutoClose
        footer: null

        width: modalWidthFor(parent)
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        padding: modalPadding

        Overlay.modal: Rectangle { color: modalOverlayColor }

        header: Item {
            implicitHeight: 46

            Rectangle {
                id: fwConfirmHeaderBg
                anchors.fill: parent
                anchors.margins: modalBorderWidth
                color: "#3A3F4B"
                radius: modalRadius
            }

            Rectangle {
                anchors.left: fwConfirmHeaderBg.left
                anchors.right: fwConfirmHeaderBg.right
                anchors.bottom: fwConfirmHeaderBg.bottom
                height: modalRadius
                color: fwConfirmHeaderBg.color
            }

            Text {
                text: fwConfirmDialog.title
                anchors.left: parent.left
                anchors.leftMargin: modalPadding
                anchors.verticalCenter: parent.verticalCenter
                color: "white"
                font.pixelSize: 18
                font.weight: Font.Medium
                horizontalAlignment: Text.AlignLeft
                verticalAlignment: Text.AlignVCenter
            }
        }

        background: Rectangle {
            color: modalBackgroundColor
            radius: modalRadius
            border.color: modalBorderColor
            border.width: modalBorderWidth
        }

        contentItem: ColumnLayout {
            spacing: 10
            width: fwConfirmDialog.width - fwConfirmDialog.leftPadding - fwConfirmDialog.rightPadding

            Text {
                text: {
                    var label = (fwUpdateTarget === "CONSOLE") ? "console" : (fwUpdateTarget === "SENSOR_LEFT") ? "left sensor" : "right sensor"
                    return "Update " + label + " firmware to " + consoleFwSelectedTag + "?"
                }
                color: "white"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Text {
                text: "File: " + consoleFwFilename + "\nThe console will reboot into DFU mode and be re-flashed."
                color: "#BDC3C7"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Item { Layout.fillWidth: true }

                Button {
                    text: "Cancel"
                    Layout.preferredWidth: 110
                    Layout.preferredHeight: 40
                    hoverEnabled: true

                    onClicked: {
                        fwConfirmDialog.close()
                        MOTIONInterface.cancelConsoleFirmwareUpdate(consoleFwToken)
                        consoleFwToken = ""
                    }

                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    background: Rectangle {
                        color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                        border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                        radius: 6
                    }
                }

                Button {
                    text: "OK"
                    Layout.preferredWidth: 110
                    Layout.preferredHeight: 40
                    hoverEnabled: true
                    enabled: consoleFwToken !== ""

                    onClicked: {
                        fwConfirmDialog.close()
                        fwProgressDialog.open()
                        MOTIONInterface.startConsoleFirmwareUpdate(consoleFwToken)
                    }

                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    background: Rectangle {
                        color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                        border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                        radius: 6
                    }
                }
            }
        }
    }

    Dialog {
        id: fwProgressDialog
        parent: contentArea
        modal: true
        title: "Firmware Update"
        standardButtons: Dialog.NoButton
        closePolicy: Popup.NoAutoClose
        footer: null

        width: modalWidthFor(parent)
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        padding: modalPadding

        Overlay.modal: Rectangle { color: modalOverlayColor }

        header: Item {
            implicitHeight: 46

            Rectangle {
                id: fwProgressHeaderBg
                anchors.fill: parent
                anchors.margins: modalBorderWidth
                color: "#3A3F4B"
                radius: modalRadius
            }

            Rectangle {
                anchors.left: fwProgressHeaderBg.left
                anchors.right: fwProgressHeaderBg.right
                anchors.bottom: fwProgressHeaderBg.bottom
                height: modalRadius
                color: fwProgressHeaderBg.color
            }

            Text {
                text: fwProgressDialog.title
                anchors.left: parent.left
                anchors.leftMargin: modalPadding
                anchors.verticalCenter: parent.verticalCenter
                color: "white"
                font.pixelSize: 18
                font.weight: Font.Medium
                horizontalAlignment: Text.AlignLeft
                verticalAlignment: Text.AlignVCenter
            }
        }

        background: Rectangle {
            color: modalBackgroundColor
            radius: modalRadius
            border.color: modalBorderColor
            border.width: modalBorderWidth
        }

        contentItem: ColumnLayout {
            spacing: 12
            width: fwProgressDialog.width - fwProgressDialog.leftPadding - fwProgressDialog.rightPadding

            Text {
                text: consoleFwStageText
                color: "white"
                font.pixelSize: 16
                Layout.fillWidth: true
            }

            ProgressBar {
                Layout.fillWidth: true
                from: 0
                to: 1
                indeterminate: consoleFwPercent < 0
                value: consoleFwPercent < 0 ? 0 : (consoleFwPercent / 100.0)
            }

            Text {
                text: consoleFwMessage
                color: "#BDC3C7"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Remaining content area (split into App Info top, Modules bottom)
        Item {
            id: contentArea
            Layout.fillWidth: true
            Layout.fillHeight: true

            RowLayout {
                id: appRow
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: parent.height * 0.33
                spacing: 16

                Rectangle {
                    id: appInfoContainer
                    Layout.preferredWidth: appRow.width / 3
                    Layout.fillWidth: false
                    Layout.fillHeight: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10

                        Text {
                            text: "Application"
                            color: "#BDC3C7"
                            font.pixelSize: 16
                            font.bold: true
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            columnSpacing: 10
                            rowSpacing: 6

                            Text { text: "App Version:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                            Text { text: "" + appVersion; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                            Text { text: "SDK Version:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                            Text { text: "" + MOTIONInterface.get_sdk_version(); color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                            Text { text: "System State:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                            Text {
                                text: {
                                    const c = MOTIONInterface.consoleConnected
                                    const l = MOTIONInterface.leftSensorConnected
                                    const r = MOTIONInterface.rightSensorConnected
                                    if (c && l && r) return "Connected"
                                    if (!c && !l && !r) return "Disconnected"
                                    return "Partially Connected"
                                }
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }

                Rectangle {
                    id: userConfigContainer
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

                    // Validators outside GridLayout so they don't occupy grid cells
                    DoubleValidator { id: valTecTrip; bottom: 0; top: 100;  decimals: 2; notation: DoubleValidator.StandardNotation }
                    DoubleValidator { id: valGain;    bottom: 0; top: 1000; decimals: 2; notation: DoubleValidator.StandardNotation }
                    DoubleValidator { id: valThresh;  bottom: 0; top: 1000; decimals: 2; notation: DoubleValidator.StandardNotation }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10

                        Text {
                            text: "User Configuration"
                            color: "#BDC3C7"
                            font.pixelSize: 16
                            font.bold: true
                        }

                        // 4-column grid: Label | Field | Label | Field
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 4
                            columnSpacing: 8
                            rowSpacing: 10

                            // Row 1: TEC_TRIP | OPT_THRESH
                            Text { text: "TEC_TRIP:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 90 }
                            TextField {
                                id: tecTripField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                placeholderText: "0.00 – 100.00"
                                validator: valTecTrip
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: userTecTrip.toFixed(2)
                                onAccepted: {
                                    let v = parseFloat(text)
                                    if (isNaN(v)) { text = ""; return }
                                    if (v < 0) v = 0
                                    if (v > 100) v = 100
                                    userTecTrip = v
                                    text = userTecTrip.toFixed(2)
                                }
                            }
                            Text { text: "OPT_THRESH:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 90 }
                            TextField {
                                id: optThreshField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                placeholderText: "0.00 – 1000.00"
                                validator: valThresh
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: userOptThresh.toFixed(2)
                                onAccepted: {
                                    let v = parseFloat(text)
                                    if (isNaN(v)) { text = ""; return }
                                    if (v < 0) v = 0
                                    if (v > 1000) v = 1000
                                    userOptThresh = v
                                    text = userOptThresh.toFixed(2)
                                }
                            }

                            // Row 2: OPT_GAIN | EE_THRESH
                            Text { text: "OPT_GAIN:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 90 }
                            TextField {
                                id: optGainField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                placeholderText: "0.00 – 1000.00"
                                validator: valGain
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: userOptGain.toFixed(2)
                                onAccepted: {
                                    let v = parseFloat(text)
                                    if (isNaN(v)) { text = ""; return }
                                    if (v < 0) v = 0
                                    if (v > 1000) v = 1000
                                    userOptGain = v
                                    text = userOptGain.toFixed(2)
                                }
                            }
                            Text { text: "EE_THRESH:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 90 }
                            TextField {
                                id: eeThreshField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                placeholderText: "0.00 – 1000.00"
                                validator: valThresh
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: userEEThresh.toFixed(2)
                                onAccepted: {
                                    let v = parseFloat(text)
                                    if (isNaN(v)) { text = ""; return }
                                    if (v < 0) v = 0
                                    if (v > 1000) v = 1000
                                    userEEThresh = v
                                    text = userEEThresh.toFixed(2)
                                }
                            }

                            // Row 3: EE_GAIN (left) | Save button (right, spans cols 3-4)
                            Text { text: "EE_GAIN:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 90 }
                            TextField {
                                id: eeGainField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                placeholderText: "0.00 – 1000.00"
                                validator: valGain
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                text: userEEGain.toFixed(2)
                                onAccepted: {
                                    let v = parseFloat(text)
                                    if (isNaN(v)) { text = ""; return }
                                    if (v < 0) v = 0
                                    if (v > 1000) v = 1000
                                    userEEGain = v
                                    text = userEEGain.toFixed(2)
                                }
                            }
                            Item { Layout.fillWidth: true }
                            Rectangle {
                                Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                width: 120
                                height: 32
                                radius: 6
                                color: saveConfigMouseArea.containsMouse ? "#27AE60" : "#2ECC71"

                                Behavior on color { ColorAnimation { duration: 150 } }

                                Text {
                                    anchors.centerIn: parent
                                    text: "Save"
                                    color: "white"
                                    font.pixelSize: 14
                                    font.bold: true
                                }

                                MouseArea {
                                    id: saveConfigMouseArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    onClicked: {
                                        MOTIONInterface.setUserConfig(
                                            parseFloat(tecTripField.text)  || 0,
                                            parseFloat(optGainField.text)  || 0,
                                            parseFloat(optThreshField.text)|| 0,
                                            parseFloat(eeGainField.text)   || 0,
                                            parseFloat(eeThreshField.text) || 0
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            RowLayout {
                id: fpgaRow
                anchors.top: appRow.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: parent.height * 0.18
                anchors.topMargin: 15

                Rectangle {
                    id: appFpgaContainer
                    Layout.preferredWidth: fpgaRow.width
                    Layout.fillWidth: false
                    Layout.fillHeight: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8

                        // ── Left narrow column: FPGAs label + indicator, refresh below ──
                        ColumnLayout {
                            Layout.preferredWidth: implicitWidth
                            Layout.minimumWidth: 110
                            Layout.fillHeight: true
                            spacing: 8

                            RowLayout {
                                spacing: 6
                                Text { text: "FPGAs"; font.pixelSize: 16; color: "#BDC3C7" }
                                Rectangle {
                                    width: 14; height: 14; radius: 7
                                    color: MOTIONInterface.consoleConnected ? "green" : "red"
                                    border.color: "black"; border.width: 1
                                }
                            }

                            Rectangle {
                                width: 30; height: 30; radius: 15
                                color: enabled ? "#2C3E50" : "#7F8C8D"
                                enabled: MOTIONInterface.consoleConnected

                                Text {
                                    text: "\u21BB"
                                    anchors.centerIn: parent
                                    font.pixelSize: 20
                                    font.family: iconFont.name
                                    color: enabled ? "white" : "#BDC3C7"
                                }

                                MouseArea {
                                    id: refreshFpgaMouseArea
                                    anchors.fill: parent
                                    enabled: parent.enabled
                                    hoverEnabled: true
                                    onClicked: refreshConsoleInfo()
                                    onEntered: if (parent.enabled) parent.color = "#34495E"
                                    onExited: parent.color = parent.enabled ? "#2C3E50" : "#7F8C8D"
                                }

                                ToolTip.visible: refreshFpgaMouseArea.containsMouse
                                ToolTip.text: "Refresh"
                                ToolTip.delay: 400
                            }

                            Item { Layout.fillHeight: true }
                        }

                        // Vertical divider
                        Rectangle { width: 1; Layout.fillHeight: true; color: "#3E4E6F" }

                        // ── TA panel ──
                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 8

                                RowLayout {
                                    spacing: 8
                                    Layout.fillWidth: true
                                    Text { text: "TA"; font.pixelSize: 16; color: "#BDC3C7" }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        width: 80; height: 28; radius: 8
                                        color: enabled ? "#E74C3C" : "#7F8C8D"
                                        enabled: MOTIONInterface.consoleConnected
                                        Text { anchors.centerIn: parent; text: "Update"; color: parent.enabled ? "white" : "#BDC3C7"; font.pixelSize: 13; font.weight: Font.Bold }
                                        MouseArea {
                                            anchors.fill: parent; enabled: parent.enabled
                                            onEntered: if (parent.enabled) parent.color = "#C0392B"
                                            onExited: if (parent.enabled) parent.color = "#E74C3C"
                                        }
                                        Behavior on color { ColorAnimation { duration: 200 } }
                                    }
                                }

                                Rectangle { Layout.fillWidth: true; height: 2; color: "#3E4E6F" }

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    columnSpacing: 10
                                    rowSpacing: 6

                                    Text { text: "FW:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 70 }
                                    Text { text: "N/A"; color: "#2ECC71"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                    Text { text: "Latest:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 70 }
                                    Text { text: "N/A"; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }
                                }

                            }
                        }

                        // Vertical divider
                        Rectangle { width: 1; Layout.fillHeight: true; color: "#3E4E6F" }

                        // ── Seed panel ──
                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 8

                                RowLayout {
                                    spacing: 8
                                    Layout.fillWidth: true
                                    Text { text: "Seed"; font.pixelSize: 16; color: "#BDC3C7" }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        width: 80; height: 28; radius: 8
                                        color: enabled ? "#E74C3C" : "#7F8C8D"
                                        enabled: MOTIONInterface.consoleConnected
                                        Text { anchors.centerIn: parent; text: "Update"; color: parent.enabled ? "white" : "#BDC3C7"; font.pixelSize: 13; font.weight: Font.Bold }
                                        MouseArea {
                                            anchors.fill: parent; enabled: parent.enabled
                                            onEntered: if (parent.enabled) parent.color = "#C0392B"
                                            onExited: if (parent.enabled) parent.color = "#E74C3C"
                                        }
                                        Behavior on color { ColorAnimation { duration: 200 } }
                                    }
                                }

                                Rectangle { Layout.fillWidth: true; height: 2; color: "#3E4E6F" }

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    columnSpacing: 10
                                    rowSpacing: 6

                                    Text { text: "FW:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 70 }
                                    Text { text: "N/A"; color: "#2ECC71"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                    Text { text: "Latest:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 70 }
                                    Text { text: "N/A"; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }
                                }

                            }
                        }

                        // Vertical divider
                        Rectangle { width: 1; Layout.fillHeight: true; color: "#3E4E6F" }

                        // ── Safety panel ──
                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 8

                                RowLayout {
                                    spacing: 8
                                    Layout.fillWidth: true
                                    Text { text: "Safety"; font.pixelSize: 16; color: "#BDC3C7" }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        width: 80; height: 28; radius: 8
                                        color: enabled ? "#E74C3C" : "#7F8C8D"
                                        enabled: MOTIONInterface.consoleConnected
                                        Text { anchors.centerIn: parent; text: "Update"; color: parent.enabled ? "white" : "#BDC3C7"; font.pixelSize: 13; font.weight: Font.Bold }
                                        MouseArea {
                                            anchors.fill: parent; enabled: parent.enabled
                                            onEntered: if (parent.enabled) parent.color = "#C0392B"
                                            onExited: if (parent.enabled) parent.color = "#E74C3C"
                                        }
                                        Behavior on color { ColorAnimation { duration: 200 } }
                                    }
                                }

                                Rectangle { Layout.fillWidth: true; height: 2; color: "#3E4E6F" }

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    columnSpacing: 10
                                    rowSpacing: 6

                                    Text { text: "FW:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 70 }
                                    Text { text: "N/A"; color: "#2ECC71"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                    Text { text: "Latest:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 70 }
                                    Text { text: "N/A"; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }
                                }

                            }
                        }
                    }
                }
            }

            Item {
                id: modulesArea
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.top: fpgaRow.bottom
                anchors.topMargin: 15

                RowLayout {
                    id: modulesRow
                    anchors.fill: parent
                    spacing: 20

                    // Console
                    Rectangle {
                        Layout.fillHeight: true
                        Layout.fillWidth: true
                        Layout.preferredWidth: modulesRow.width / 3
                        color: "#1E1E20"
                        radius: 10
                        border.color: "#3E4E6F"
                        border.width: 2

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 10

                            RowLayout {
                                spacing: 8
                                Layout.fillWidth: true

                                Text { text: "Console"; font.pixelSize: 16; color: "#BDC3C7" }
                                Rectangle {
                                    width: 14
                                    height: 14
                                    radius: 7
                                    color: MOTIONInterface.consoleConnected ? "green" : "red"
                                    border.color: "black"
                                    border.width: 1
                                }

                                Item { Layout.fillWidth: true }

                                Rectangle {
                                    width: 30
                                    height: 30
                                    radius: 15
                                    color: enabled ? "#2C3E50" : "#7F8C8D"
                                    enabled: MOTIONInterface.consoleConnected

                                    Text {
                                        text: "\u21BB"
                                        anchors.centerIn: parent
                                        font.pixelSize: 20
                                        font.family: iconFont.name
                                        color: enabled ? "white" : "#BDC3C7"
                                    }

                                    MouseArea {
                                        id: refreshConsoleMouseArea
                                        anchors.fill: parent
                                        enabled: parent.enabled
                                        hoverEnabled: true
                                        onClicked: refreshConsoleInfo()
                                        onEntered: if (parent.enabled) parent.color = "#34495E"
                                        onExited: parent.color = parent.enabled ? "#2C3E50" : "#7F8C8D"
                                    }

                                    ToolTip.visible: refreshConsoleMouseArea.containsMouse
                                    ToolTip.text: "Refresh"
                                    ToolTip.delay: 400
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 2; color: "#3E4E6F" }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 10
                                rowSpacing: 6

                                Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: consoleDeviceId; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Board Rev ID:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: consoleBoardRevId; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Firmware:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: consoleFirmwareVersion; color: "#2ECC71"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Latest Release:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: consoleLatestFirmware; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Published:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: consoleLatestFirmwareDate; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Select Release:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                ComboBox {
                                    id: consoleLatestCombo
                                    model: consoleReleasesModel.concat(["Upload File..."])
                                    currentIndex: consoleLatestIndex
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    enabled: MOTIONInterface.consoleConnected && consoleReleasesModel.length > 0
                                    onCurrentIndexChanged: consoleLatestIndex = currentIndex
                                }
                            }

                            Item { Layout.fillHeight: true }

                            Rectangle {
                                Layout.fillWidth: true
                                height: 40
                                radius: 10
                                color: enabled ? "#E74C3C" : "#7F8C8D"
                                enabled: MOTIONInterface.consoleConnected
                                    && consoleFirmwareVersion !== "N/A"
                                    && consoleDeviceId !== "N/A"
                                    && consoleBoardRevId !== "N/A"
                                    && consoleReleasesModel.length > 0
                                    && !MOTIONInterface.consoleFirmwareUpdateBusy

                                Text {
                                    text: "Update Firmware"
                                    anchors.centerIn: parent
                                    color: parent.enabled ? "white" : "#BDC3C7"
                                    font.pixelSize: 18
                                    font.weight: Font.Bold
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: parent.enabled
                                    onClicked: {
                                        var tag = consoleLatestCombo.currentText
                                        if (!tag || tag === "")
                                            tag = consoleLatestFirmware
                                        if (tag === "Upload File...") {
                                            fwUploadTarget = "CONSOLE"
                                            fwUploadDialog.open()
                                            return
                                        }
                                        consoleFwPercent = -1
                                        consoleFwMessage = ""
                                        consoleFwStageText = "Starting…"
                                        MOTIONInterface.beginConsoleFirmwareDownload(tag)
                                        fwProgressDialog.open()
                                    }
                                    onEntered: if (parent.enabled) parent.color = "#C0392B"
                                    onExited: if (parent.enabled) parent.color = "#E74C3C"
                                }

                                Behavior on color { ColorAnimation { duration: 200 } }
                            }
                        }
                    }

                    // Sensor Left
                    Rectangle {
                        Layout.fillHeight: true
                        Layout.fillWidth: true
                        Layout.preferredWidth: modulesRow.width / 3
                        color: "#1E1E20"
                        radius: 10
                        border.color: "#3E4E6F"
                        border.width: 2

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 10

                            RowLayout {
                                spacing: 8
                                Layout.fillWidth: true

                                Text { text: "Sensor (L)"; font.pixelSize: 16; color: "#BDC3C7" }
                                Rectangle {
                                    width: 14
                                    height: 14
                                    radius: 7
                                    color: MOTIONInterface.leftSensorConnected ? "green" : "red"
                                    border.color: "black"
                                    border.width: 1
                                }

                                Item { Layout.fillWidth: true }

                                Rectangle {
                                    width: 30
                                    height: 30
                                    radius: 15
                                    color: enabled ? "#2C3E50" : "#7F8C8D"
                                    enabled: MOTIONInterface.leftSensorConnected

                                    Text {
                                        text: "\u21BB"
                                        anchors.centerIn: parent
                                        font.pixelSize: 20
                                        font.family: iconFont.name
                                        color: enabled ? "white" : "#BDC3C7"
                                    }

                                    MouseArea {
                                        id: refreshSensorLeftMouseArea
                                        anchors.fill: parent
                                        enabled: parent.enabled
                                        hoverEnabled: true
                                        onClicked: refreshSensorInfo("SENSOR_LEFT")
                                        onEntered: if (parent.enabled) parent.color = "#34495E"
                                        onExited: parent.color = parent.enabled ? "#2C3E50" : "#7F8C8D"
                                    }

                                    ToolTip.visible: refreshSensorLeftMouseArea.containsMouse
                                    ToolTip.text: "Refresh"
                                    ToolTip.delay: 400
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 2; color: "#3E4E6F" }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 10
                                rowSpacing: 6

                                Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: leftSensorDeviceId; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Firmware:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: leftSensorFirmwareVersion; color: "#2ECC71"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Latest Release:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: leftLatestFirmware; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Published:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: leftLatestFirmwareDate; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Select Release:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                ComboBox {
                                    id: leftLatestCombo
                                    model: leftReleasesModel.concat(["Upload File..."])
                                    currentIndex: leftLatestIndex
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    enabled: MOTIONInterface.leftSensorConnected && leftReleasesModel.length > 0
                                    onCurrentIndexChanged: leftLatestIndex = currentIndex
                                }
                            }

                            Item { Layout.fillHeight: true }

                            Rectangle {
                                Layout.fillWidth: true
                                height: 40
                                radius: 10
                                color: enabled ? "#E74C3C" : "#7F8C8D"
                                enabled: MOTIONInterface.leftSensorConnected
                                    && leftSensorFirmwareVersion !== "N/A"
                                    && leftSensorDeviceId !== "N/A"

                                Text {
                                    text: "Update Firmware"
                                    anchors.centerIn: parent
                                    color: parent.enabled ? "white" : "#BDC3C7"
                                    font.pixelSize: 18
                                    font.weight: Font.Bold
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: parent.enabled
                                    onClicked: {
                                        var tag = leftLatestCombo.currentText
                                        if (!tag || tag === "")
                                            tag = leftLatestFirmware
                                        if (tag === "Upload File...") {
                                            fwUploadTarget = "SENSOR_LEFT"
                                            fwUploadDialog.open()
                                            return
                                        }
                                        consoleFwPercent = -1
                                        consoleFwMessage = ""
                                        consoleFwStageText = "Starting…"
                                        MOTIONInterface.beginDeviceFirmwareDownload("SENSOR_LEFT", tag)
                                        fwProgressDialog.open()
                                    }
                                    onEntered: if (parent.enabled) parent.color = "#C0392B"
                                    onExited: if (parent.enabled) parent.color = "#E74C3C"
                                }

                                Behavior on color { ColorAnimation { duration: 200 } }
                            }
                        }
                    }

                    // Sensor Right
                    Rectangle {
                        Layout.fillHeight: true
                        Layout.fillWidth: true
                        Layout.preferredWidth: modulesRow.width / 3
                        color: "#1E1E20"
                        radius: 10
                        border.color: "#3E4E6F"
                        border.width: 2

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 10

                            RowLayout {
                                spacing: 8
                                Layout.fillWidth: true

                                Text { text: "Sensor (R)"; font.pixelSize: 16; color: "#BDC3C7" }
                                Rectangle {
                                    width: 14
                                    height: 14
                                    radius: 7
                                    color: MOTIONInterface.rightSensorConnected ? "green" : "red"
                                    border.color: "black"
                                    border.width: 1
                                }

                                Item { Layout.fillWidth: true }

                                Rectangle {
                                    width: 30
                                    height: 30
                                    radius: 15
                                    color: enabled ? "#2C3E50" : "#7F8C8D"
                                    enabled: MOTIONInterface.rightSensorConnected

                                    Text {
                                        text: "\u21BB"
                                        anchors.centerIn: parent
                                        font.pixelSize: 20
                                        font.family: iconFont.name
                                        color: enabled ? "white" : "#BDC3C7"
                                    }

                                    MouseArea {
                                        id: refreshSensorRightMouseArea
                                        anchors.fill: parent
                                        enabled: parent.enabled
                                        hoverEnabled: true
                                        onClicked: refreshSensorInfo("SENSOR_RIGHT")
                                        onEntered: if (parent.enabled) parent.color = "#34495E"
                                        onExited: parent.color = parent.enabled ? "#2C3E50" : "#7F8C8D"
                                    }

                                    ToolTip.visible: refreshSensorRightMouseArea.containsMouse
                                    ToolTip.text: "Refresh"
                                    ToolTip.delay: 400
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 2; color: "#3E4E6F" }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 10
                                rowSpacing: 6

                                Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: rightSensorDeviceId; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Firmware:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: rightSensorFirmwareVersion; color: "#2ECC71"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Latest Release:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: rightLatestFirmware; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Published:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                Text { text: rightLatestFirmwareDate; color: "#3498DB"; font.pixelSize: 14; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text { text: "Select Release:"; color: "#BDC3C7"; font.pixelSize: 14; horizontalAlignment: Text.AlignLeft; Layout.preferredWidth: 120 }
                                ComboBox {
                                    id: rightLatestCombo
                                    model: rightReleasesModel.concat(["Upload File..."])
                                    currentIndex: rightLatestIndex
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    enabled: MOTIONInterface.rightSensorConnected && rightReleasesModel.length > 0
                                    onCurrentIndexChanged: rightLatestIndex = currentIndex
                                }
                            }

                            Item { Layout.fillHeight: true }

                            Rectangle {
                                Layout.fillWidth: true
                                height: 40
                                radius: 10
                                color: enabled ? "#E74C3C" : "#7F8C8D"
                                enabled: MOTIONInterface.rightSensorConnected
                                    && rightSensorFirmwareVersion !== "N/A"
                                    && rightSensorDeviceId !== "N/A"

                                Text {
                                    text: "Update Firmware"
                                    anchors.centerIn: parent
                                    color: parent.enabled ? "white" : "#BDC3C7"
                                    font.pixelSize: 18
                                    font.weight: Font.Bold
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: parent.enabled
                                    onClicked: {
                                        var tag = rightLatestCombo.currentText
                                        if (!tag || tag === "")
                                            tag = rightLatestFirmware
                                        if (tag === "Upload File...") {
                                            fwUploadTarget = "SENSOR_RIGHT"
                                            fwUploadDialog.open()
                                            return
                                        }
                                        consoleFwPercent = -1
                                        consoleFwMessage = ""
                                        consoleFwStageText = "Starting…"
                                        MOTIONInterface.beginDeviceFirmwareDownload("SENSOR_RIGHT", tag)
                                        fwProgressDialog.open()
                                    }
                                    onEntered: if (parent.enabled) parent.color = "#C0392B"
                                    onExited: if (parent.enabled) parent.color = "#E74C3C"
                                }

                                Behavior on color { ColorAnimation { duration: 200 } }
                            }
                        }
                    }
                }
            }
        }
    }

    FontLoader {
        id: iconFont
        source: "../assets/fonts/keenicons-outline.ttf"
    }

    // Busy overlay shown while reading user config from device
    Rectangle {
        anchors.fill: parent
        radius: parent.radius
        color: "#80000000"
        visible: userConfigLoading
        z: 100

        BusyIndicator {
            anchors.centerIn: parent
            running: userConfigLoading
            width: 64
            height: 64
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.verticalCenter
            anchors.topMargin: 44
            text: "Reading device configuration…"
            color: "#BDC3C7"
            font.pixelSize: 14
        }
    }
}
