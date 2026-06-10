import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import OpenMotion 1.0 

import "../components"

Rectangle {
    id: page1
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // Properties for dynamic data
    property string firmwareVersion: "N/A"
    property string deviceId: "N/A"
    property real sensor_temperature: 0.0
    property real amb_temperature: 0.0
    property int accel_x: 0.0
    property int accel_y: 0.0
    property int accel_z: 0.0
    property int gyro_x: 0.0
    property int gyro_y: 0.0
    property int gyro_z: 0.0

    // Serial number properties for each camera
    property string cam1_sn: ""
    property string cam2_sn: ""
    property string cam3_sn: ""
    property string cam4_sn: ""
    property string cam5_sn: ""
    property string cam6_sn: ""
    property string cam7_sn: ""
    property string cam8_sn: ""

    // Camera power status properties
    property bool camera1_powered: false
    property bool camera2_powered: false
    property bool camera3_powered: false
    property bool camera4_powered: false
    property bool camera5_powered: false
    property bool camera6_powered: false
    property bool camera7_powered: false
    property bool camera8_powered: false

    // Fan control properties
    property bool fanControlOn: false

    ListModel {
        id: cameraStatusModel
        ListElement { label: "Camera 1"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 2"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 3"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 4"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 5"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 6"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 7"; status: "Not Tested"; color: "gray" }
        ListElement { label: "Camera 8"; status: "Not Tested"; color: "gray" }
    }

    function updateStates() {
        // console.log("Sensor Updating all states...")
        
        let isConnected = (sensorSelector.currentIndex === 0)
            ? MOTIONInterface.leftSensorConnected
            : MOTIONInterface.rightSensorConnected

        if (!isConnected) {
            // console.log("Selected sensor is not connected. Skipping update.")
            return
        }

        let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
        // console.log("Sensor Updating all states for", sensor_tag);
        
        MOTIONInterface.querySensorInfo(sensor_tag)
        MOTIONInterface.querySensorTemperature(sensor_tag)
        MOTIONInterface.querySensorAccelerometer(sensor_tag)
        MOTIONInterface.queryCameraPowerStatus(sensor_tag)
        //MOTIONInterface.queryTriggerInfo()
    }

    // Run refresh logic immediately on page load if Sensor is already connected
    Component.onCompleted: {
        sensorSelector.currentIndex = 0 // default
        if (MOTIONInterface.leftSensorConnected || MOTIONInterface.rightSensorConnected) {
            // console.log("Page Loaded - Sensor Already Connected. Fetching Info...");
            updateStates();
            // Also query camera power status for the selected sensor
            let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
            MOTIONInterface.queryCameraPowerStatus(sensor_tag);
            // Start fan status polling
            fanStatusTimer.start();
        }
    }

    Timer {
        id: infoTimer
        interval: 1500   // Delay to ensure Sensor is stable before fetching info
        running: false
        onTriggered: {
            // console.log("Fetching Firmware Version and Device ID...")
            updateStates()
        }
    }

    Timer {
        id: fanStatusTimer
        interval: 1000   // Poll fan status every second
        running: false
        repeat: true
        onTriggered: {
            let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
            let isConnected = (sensorSelector.currentIndex === 0)
                ? MOTIONInterface.leftSensorConnected
                : MOTIONInterface.rightSensorConnected;
            
            if (isConnected) {
                let currentFanStatus = MOTIONInterface.getFanControlStatus(sensor_tag);
                if (currentFanStatus !== fanControlOn) {
                    fanControlOn = currentFanStatus;
                    // console.log("Fan status updated:", currentFanStatus ? "ON" : "OFF");
                }
            }
        }
    }

    Connections {
        target: MOTIONInterface

        // Handle Sensor Connected state
        function onConnectionStatusChanged() {
            if (MOTIONInterface.leftSensorConnected || MOTIONInterface.rightSensorConnected) {
                infoTimer.start()          // One-time info fetch
                // Automatically query camera power status when sensor connects
                let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                MOTIONInterface.queryCameraPowerStatus(sensor_tag);
                // Start fan status polling
                fanStatusTimer.start();
            } else {
                // console.log("Sensor Disconnected - Clearing Data...")
                firmwareVersion = "N/A"
                deviceId = "N/A"
                sensor_temperature = 0.0
                amb_temperature = 0.0
                
                pingResult.text = ""
                echoResult.text = ""
                toggleLedResult.text = ""
                fanControlResult.text = ""
                
                // Clear camera power status when disconnected
                camera1_powered = false;
                camera2_powered = false;
                camera3_powered = false;
                camera4_powered = false;
                camera5_powered = false;
                camera6_powered = false;
                camera7_powered = false;
                camera8_powered = false;
                
                // Clear fan control status and stop polling
                fanControlOn = false;
                fanStatusTimer.stop();
            }
        }

        // Handle device info response
        function onSensorDeviceInfoReceived(fwVersion, devId) {
            firmwareVersion = fwVersion
            deviceId = devId
        }

        // Handle temperature updates
        function onTemperatureSensorUpdated(imu_temp) {
            sensor_temperature = imu_temp
            amb_temperature = 0
        }
 
        function onAccelerometerSensorUpdated(x, y, z) {
            accel_x = x
            accel_y = y
            accel_z = z
        }
 
        function onGyroscopeSensorUpdated(x, y, z) {
            gyro_x = x
            gyro_y = y
            gyro_z = z
        }

        function onCameraConfigUpdated(bitmask, passed) {
            for (let i = 0; i < 8; i++) {
                if ((bitmask & (1 << i)) !== 0) {
                    cameraStatusModel.set(i, {
                        label: "Camera " + (i + 1),
                        status: passed ? "Pass" : "Fail",
                        color: passed ? "green" : "red"
                    });
                }
            }
        }

        function onHistogramCaptureCompleted(cameraIndex, weightedMean, std_dev, result) {
            // result: "PASS" | "FAIL" | "LOW_LIGHT"
            let statusColor = "green"
            if (result === "FAIL") statusColor = "red"
            else if (result === "LOW_LIGHT") statusColor = "grey"
            let statusText = "μ: " + weightedMean.toFixed(1) + " 𝜎: " + std_dev.toFixed(1)
            if (result === "LOW_LIGHT") {
                statusText += " Low Light"
            } else if (result === "FAIL") {
                statusText += " ⚠"
            }
            cameraStatusModel.set(cameraIndex, {
                label: "Camera " + (cameraIndex + 1),
                status: statusText,
                color: statusColor
            });
        }

        function onCsvOutputDirectoryChanged(directory) {
            // Update the CSV output path text when directory changes
            csvOutputPathText.text = directory
        }

        function onCameraPowerStatusUpdated(powerStatusList) {
            // console.log("Power status updated:", powerStatusList);
            // Store power status globally for use in circle colors
            for (let i = 0; i < 8; i++) {
                const isPowered = powerStatusList[i] || false;
                // Store in a global property that can be accessed by the circles
                page1["camera" + (i + 1) + "_powered"] = isPowered;
            }
        }
    }

    // Dialog for CSV output directory selection
    Dialog {
        id: csvFolderDialog
        title: "Select CSV Output Directory"
        width: 500
        height: 200
        modal: true
        
        property alias folderPath: folderPathInput.text
        
        ColumnLayout {
            anchors.fill: parent
            spacing: 20
            
            Text {
                text: "Enter the folder path where CSV files should be saved:"
                color: "#BDC3C7"
                font.pixelSize: 14
                wrapMode: Text.WordWrap
            }
            
            TextField {
                id: folderPathInput
                text: MOTIONInterface.csvOutputDirectory
                Layout.fillWidth: true
                placeholderText: "Enter folder path..."
                selectByMouse: true
                
                background: Rectangle {
                    color: "#3A3F4B"
                    border.color: "#BDC3C7"
                    border.width: 1
                    radius: 4
                }
                
                color: "#BDC3C7"
                font.pixelSize: 12
            }
            
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                
                Button {
                    text: "Cancel"
                    Layout.preferredWidth: 80
                    Layout.preferredHeight: 30
                    
                    background: Rectangle {
                        color: parent.hovered ? "#E74C3C" : "#3A3F4B"
                        radius: 4
                        border.color: "#BDC3C7"
                    }
                    
                    contentItem: Text {
                        text: parent.text
                        color: "#BDC3C7"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: 12
                    }
                    
                    onClicked: csvFolderDialog.close()
                }
                
                Button {
                    text: "OK"
                    Layout.preferredWidth: 80
                    Layout.preferredHeight: 30
                    
                    background: Rectangle {
                        color: parent.hovered ? "#27AE60" : "#3A3F4B"
                        radius: 4
                        border.color: "#BDC3C7"
                    }
                    
                    contentItem: Text {
                        text: parent.text
                        color: "#BDC3C7"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: 12
                    }
                    
                    onClicked: {
                        if (folderPathInput.text.trim() !== "") {
                            MOTIONInterface.setCsvOutputDirectory(folderPathInput.text.trim())
                            csvFolderDialog.close()
                        }
                    }
                }
            }
        }
    }

    // NVCM permanent-flash confirmation
    Dialog {
        id: nvcmConfirmDialog
        title: "Permanent NVCM Flash"
        width: 520
        height: 280
        modal: true

        property string sensorTag: "left"
        property int cameraMask: 0
        property string cameraLabel: ""

        ColumnLayout {
            anchors.fill: parent
            spacing: 16

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: "#E74C3C"
                font.pixelSize: 14
                font.bold: true
                text: "This permanently programs the FPGA's one-time-" +
                      "programmable memory and CANNOT be undone."
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: "#BDC3C7"
                font.pixelSize: 13
                text: "Target: " + nvcmConfirmDialog.cameraLabel + " on the " +
                      nvcmConfirmDialog.sensorTag.toUpperCase() + " sensor.\n\n" +
                      "Each camera takes about 5 minutes to burn and verify; " +
                      "\"All Cameras\" takes about 40 minutes. The app must stay " +
                      "connected for the whole burn."
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10

                Button {
                    text: "Cancel"
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
                    onClicked: nvcmConfirmDialog.close()
                }
                Button {
                    text: "Flash NVCM"
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 32
                    background: Rectangle {
                        color: parent.hovered ? "#E74C3C" : "#3A3F4B"
                        radius: 4
                        border.color: "#E74C3C"
                    }
                    contentItem: Text {
                        text: parent.text
                        color: "#E74C3C"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.bold: true
                    }
                    onClicked: {
                        nvcmConfirmDialog.close()
                        MOTIONInterface.flashNvcm(nvcmConfirmDialog.sensorTag,
                                                  nvcmConfirmDialog.cameraMask)
                    }
                }
            }
        }
    }

    // NVCM result summary
    Dialog {
        id: nvcmSummaryDialog
        title: "NVCM Flash Result"
        width: 520
        height: 300
        modal: true

        property bool resultOk: false
        property string summaryText: ""

        ColumnLayout {
            anchors.fill: parent
            spacing: 16

            Text {
                text: nvcmSummaryDialog.resultOk ? "All cameras PASSED"
                                                 : "One or more cameras FAILED"
                color: nvcmSummaryDialog.resultOk ? "#27AE60" : "#E74C3C"
                font.pixelSize: 15
                font.bold: true
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Text {
                    text: nvcmSummaryDialog.summaryText
                    color: "#BDC3C7"
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
            Button {
                text: "Close"
                Layout.alignment: Qt.AlignRight
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
                onClicked: nvcmSummaryDialog.close()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Title
        Text {
            text: "Sensor Module Unit Tests"
            font.pixelSize: 20
            font.weight: Font.Bold
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            Layout.alignment: Qt.AlignHCenter
        }

        // Content Section (no outer box/border)
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "transparent"
            radius: 0
            border.width: 0

            RowLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 10

                // Vertical Stack Section
                ColumnLayout {
                    Layout.fillHeight: true
                    Layout.preferredWidth: parent.width * 0.65
                    spacing: 10
                    
                    // Communication Tests Box
                    Rectangle {
                        width: 650
                        height: 170
                        radius: 6
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        Text {
                            text: "Communication Tests"
                            color: "#BDC3C7"
                            font.pixelSize: 18
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 5  // 5px spacing from the top
                        }

                        // Content for comms tests
                        ColumnLayout {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.leftMargin: 20   
                            anchors.topMargin: 40    
                            spacing: 10

                            // Top row: Ping, Echo, Toggle LED
                            RowLayout {
                                spacing: 10
                                
                                // Ping Button and Result
                                Button {
                                    id: pingButton
                                    text: "Ping"
                                    Layout.preferredWidth: 80
                                    Layout.preferredHeight: 50
                                    hoverEnabled: true
                                    enabled: {
                                        if (sensorSelector.currentIndex === 0) {
                                            return MOTIONInterface.leftSensorConnected
                                        } else {
                                            return MOTIONInterface.rightSensorConnected
                                        }
                                    }

                                    contentItem: Text {
                                        text: parent.text
                                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    background: Rectangle {
                                        color: {
                                            if (!parent.enabled) {
                                                return "#3A3F4B"
                                            }
                                            return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                        }
                                        radius: 4
                                        border.color: {
                                            if (!parent.enabled) {
                                                return "#7F8C8D"
                                            }
                                            return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                        }
                                    }

                                    onClicked: {
                                        let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                                        if(MOTIONInterface.sendPingCommand(sensor_tag)){                                        
                                            pingResult.text = "Ping SUCCESS"
                                            pingResult.color = "green"
                                        }else{
                                            pingResult.text = "Ping FAILED"
                                            pingResult.color = "red"
                                        }
                                    }
                                }
                                Text {
                                    id: pingResult
                                    Layout.preferredWidth: 80
                                    text: ""
                                    color: "#BDC3C7"
                                    Layout.alignment: Qt.AlignVCenter
                                }

                                // Echo Button and Result
                                Button {
                                    id: echoButton
                                    text: "Echo"
                                    Layout.preferredWidth: 80
                                    Layout.preferredHeight: 50
                                    hoverEnabled: true
                                    enabled: {
                                        if (sensorSelector.currentIndex === 0) {
                                            return MOTIONInterface.leftSensorConnected
                                        } else {
                                            return MOTIONInterface.rightSensorConnected
                                        }
                                    }

                                    contentItem: Text {
                                        text: parent.text
                                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    background: Rectangle {
                                        color: {
                                            if (!parent.enabled) {
                                                return "#3A3F4B"
                                            }
                                            return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                        }
                                        radius: 4
                                        border.color: {
                                            if (!parent.enabled) {
                                                return "#7F8C8D"
                                            }
                                            return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                        }
                                    }

                                    onClicked: {
                                        let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                                        if(MOTIONInterface.sendEchoCommand(sensor_tag)) {
                                            echoResult.text = "Echo SUCCESS"
                                            echoResult.color = "green"
                                        } else {
                                            echoResult.text = "Echo FAILED"
                                            echoResult.color = "red"
                                        }
                                    }
                                }
                                Text {
                                    id: echoResult
                                    Layout.preferredWidth: 80
                                    text: ""
                                    color: "#BDC3C7"
                                    Layout.alignment: Qt.AlignVCenter
                                }

                                // Toggle LED Button and Result
                                Button {
                                    id: ledButton
                                    text: "Toggle LED"
                                    Layout.preferredWidth: 80
                                    Layout.preferredHeight: 50
                                    hoverEnabled: true
                                    enabled: {
                                        if (sensorSelector.currentIndex === 0) {
                                            return MOTIONInterface.leftSensorConnected
                                        } else {
                                            return MOTIONInterface.rightSensorConnected
                                        }
                                    }

                                    contentItem: Text {
                                        text: parent.text
                                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    background: Rectangle {
                                        color: {
                                            if (!parent.enabled) {
                                                return "#3A3F4B"
                                            }
                                            return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                        }
                                        radius: 4
                                        border.color: {
                                            if (!parent.enabled) {
                                                return "#7F8C8D"
                                            }
                                            return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                        }
                                    }

                                    onClicked: {
                                        let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                                        if(MOTIONInterface.sendLedToggleCommand(sensor_tag)) {
                                            toggleLedResult.text = "LED Toggled"
                                            toggleLedResult.color = "green"
                                        } else {
                                            toggleLedResult.text = "LED Toggle FAILED"
                                            toggleLedResult.color = "red"
                                        }
                                    }
                                }
                                Text {
                                    id: toggleLedResult
                                    Layout.preferredWidth: 80
                                    color: "#BDC3C7"
                                    text: ""
                                }
                            }

                            // Bottom row: Fan Control
                            RowLayout {
                                spacing: 10
                                
                                // Fan Control Button and Result
                                Button {
                                    id: fanControlButton
                                    text: fanControlOn ? "Fan OFF" : "Fan ON"
                                    Layout.preferredWidth: 80
                                    Layout.preferredHeight: 50
                                    hoverEnabled: true
                                    enabled: {
                                        if (sensorSelector.currentIndex === 0) {
                                            return MOTIONInterface.leftSensorConnected
                                        } else {
                                            return MOTIONInterface.rightSensorConnected
                                        }
                                    }

                                    contentItem: Text {
                                        text: parent.text
                                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    background: Rectangle {
                                        color: {
                                            if (!parent.enabled) {
                                                return "#3A3F4B"
                                            }
                                            return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                        }
                                        radius: 4
                                        border.color: {
                                            if (!parent.enabled) {
                                                return "#7F8C8D"
                                            }
                                            return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                        }
                                    }

                                    onClicked: {
                                        let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                                        let newFanState = !fanControlOn;
                                        
                                        if (MOTIONInterface.setFanControl(sensor_tag, newFanState)) {
                                            fanControlOn = newFanState;
                                            fanControlResult.text = newFanState ? "Fan ON" : "Fan OFF";
                                            fanControlResult.color = newFanState ? "green" : "orange";
                                        } else {
                                            fanControlResult.text = "Fan Control FAILED";
                                            fanControlResult.color = "red";
                                        }
                                    }
                                }
                                Text {
                                    id: fanControlResult
                                    Layout.preferredWidth: 80
                                    color: "#BDC3C7"
                                    text: ""
                                }
                            }
                        }
                    }
                    
                    // Camera Tests
                    Rectangle {
                        width: 650
                        height: 415
                        radius: 6
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        Text {
                            text: "Camera Tests"
                            color: "#BDC3C7"
                            font.pixelSize: 18
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 5  // 5px spacing from the top
                        }
                        
                        // Content for Camera Tests
                        GridLayout {
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.top: parent.top
                            anchors.topMargin: 10
                            columns: 5
                            rowSpacing: 8
                            columnSpacing: 8

                            // Clear button positioned above the grid
                            Button {
                                id: clearSerialNumbersButton
                                text: "Clear All"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 30
                                Layout.columnSpan: 5
                                Layout.alignment: Qt.AlignRight
                                Layout.topMargin: 10
                                hoverEnabled: true
                                contentItem: Text {
                                    text: parent.text
                                    color: "#BDC3C7"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    font.pixelSize: 12
                                }
                                background: Rectangle {
                                    color: parent.hovered ? "#E74C3C" : "#3A3F4B"
                                    radius: 4
                                    border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                }
                                onClicked: {
                                    // Clear all serial number fields
                                    page1.cam1_sn = "";
                                    page1.cam2_sn = "";
                                    page1.cam3_sn = "";
                                    page1.cam4_sn = "";
                                    page1.cam5_sn = "";
                                    page1.cam6_sn = "";
                                    page1.cam7_sn = "";
                                    page1.cam8_sn = "";
                                    
                                    // Reset all camera statuses to "Not Tested"
                                    for (let i = 0; i < cameraStatusModel.count; i++) {
                                        cameraStatusModel.set(i, {
                                            label: "Camera " + (i + 1),
                                            status: "Not Tested",
                                            color: "gray"
                                        });
                                    }
                                }
                            }

                            // Camera Test Status Table
                            GridLayout {
                                columns: 2
                                columnSpacing: 15
                                rowSpacing: 6
                                Layout.columnSpan: 5
                                Layout.alignment: Qt.AlignHCenter

                                // Custom order: row-wise produces [0,7,1,6,2,5,3,4]
                                Repeater {
                                    model: 8
                                    delegate: RowLayout {
                                        spacing: 6
                                        // Map visual position to camera index per desired layout
                                        property int mappedIndex: (
                                            index === 0 ? 0 :
                                            index === 1 ? 7 :
                                            index === 2 ? 1 :
                                            index === 3 ? 6 :
                                            index === 4 ? 2 :
                                            index === 5 ? 5 :
                                            index === 6 ? 3 : 4)
                                        // Determine which grid column this row will occupy
                                        property bool isLeftColumn: (index % 2) === 0

                                        // OUTER: Serial number field (left side rows)
                                        TextField {
                                            visible: parent.isLeftColumn
                                            Layout.preferredWidth: 110
                                            Layout.preferredHeight: 28
                                            maximumLength: 9
                                            placeholderText: text.length === 0 ? ("SN #" + (parent.mappedIndex + 1)) : ""
                                            color: "#BDC3C7"
                                            topPadding: 2
                                            bottomPadding: 2
                                            leftPadding: 6
                                            rightPadding: 6
                                            text: {
                                                // Bind to the appropriate camera serial number property
                                                let camNum = parent.mappedIndex + 1;
                                                if (camNum === 1) return page1.cam1_sn;
                                                if (camNum === 2) return page1.cam2_sn;
                                                if (camNum === 3) return page1.cam3_sn;
                                                if (camNum === 4) return page1.cam4_sn;
                                                if (camNum === 5) return page1.cam5_sn;
                                                if (camNum === 6) return page1.cam6_sn;
                                                if (camNum === 7) return page1.cam7_sn;
                                                if (camNum === 8) return page1.cam8_sn;
                                                return "";
                                            }
                                            onTextChanged: {
                                                // Update the corresponding property when text changes
                                                let camNum = parent.mappedIndex + 1;
                                                if (camNum === 1) page1.cam1_sn = text;
                                                if (camNum === 2) page1.cam2_sn = text;
                                                if (camNum === 3) page1.cam3_sn = text;
                                                if (camNum === 4) page1.cam4_sn = text;
                                                if (camNum === 5) page1.cam5_sn = text;
                                                if (camNum === 6) page1.cam6_sn = text;
                                                if (camNum === 7) page1.cam7_sn = text;
                                                if (camNum === 8) page1.cam8_sn = text;
                                            }
                                            background: Rectangle {
                                                radius: 4
                                                color: "#2C3E50"
                                                border.color: "#3E4E6F"
                                            }
                                        }

                                        // Left-side status (visible for left column rows)
                                        RowLayout {
                                            visible: parent.isLeftColumn
                                            spacing: 4
                                            Layout.preferredWidth: 90
                                            property int camIndex: parent.mappedIndex
                                            
                                            Rectangle {
                                                width: 12
                                                height: 12
                                                radius: 6
                                                color: cameraStatusModel.get(parent.camIndex).color
                                                border.color: "#BDC3C7"
                                                border.width: 1
                                                Layout.alignment: Qt.AlignVCenter
                                            }
                                            
                                            Text {
                                                text: cameraStatusModel.get(parent.camIndex).status
                                                color: cameraStatusModel.get(parent.camIndex).color
                                                font.pixelSize: 14
                                                Layout.fillWidth: true
                                                horizontalAlignment: Text.AlignCenter
                                            }
                                        }

                                        // Camera number badge (always visible)
                                        Item {
                                            Layout.preferredWidth: 75
                                            Layout.alignment: Qt.AlignHCenter
                                            width: 75
                                            height: 28
                                            Rectangle {
                                                width: 24
                                                height: 24
                                                radius: 12
                                                anchors.horizontalCenter: parent.horizontalCenter
                                                color: "#2C3E50"
                                                border.color: "#BDC3C7"
                                                border.width: 1
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: (parent.parent.parent.mappedIndex + 1)
                                color: "#BDC3C7"
                                                    font.pixelSize: 14
                                                    horizontalAlignment: Text.AlignHCenter
                                                    verticalAlignment: Text.AlignVCenter
                                                }
                                            }
                                        }

                                        // Right-side status (visible for right column rows)
                                        RowLayout {
                                            visible: !parent.isLeftColumn
                                            spacing: 4
                                            Layout.preferredWidth: 90
                                            property int camIndex: parent.mappedIndex
                                            
                                            Rectangle {
                                                width: 12
                                                height: 12
                                                radius: 6
                                                color: cameraStatusModel.get(parent.camIndex).color
                                                border.color: "#BDC3C7"
                                                border.width: 1
                                                Layout.alignment: Qt.AlignVCenter
                                            }
                                            
                                            Text {
                                                text: cameraStatusModel.get(parent.camIndex).status
                                                color: cameraStatusModel.get(parent.camIndex).color
                                                font.pixelSize: 14
                                                Layout.fillWidth: true
                                                horizontalAlignment: Text.AlignLeft
                                            }
                                        }

                                        // OUTER: Serial number field (right side rows)
                                        TextField {
                                            visible: !parent.isLeftColumn
                                            Layout.preferredWidth: 110
                                            Layout.preferredHeight: 28
                                            maximumLength: 9
                                            placeholderText: text.length === 0 ? ("SN #" + (parent.mappedIndex + 1)) : ""
                                            color: "#BDC3C7"
                                            topPadding: 2
                                            bottomPadding: 2
                                            leftPadding: 6
                                            rightPadding: 6
                                            text: {
                                                // Bind to the appropriate camera serial number property
                                                let camNum = parent.mappedIndex + 1;
                                                if (camNum === 1) return page1.cam1_sn;
                                                if (camNum === 2) return page1.cam2_sn;
                                                if (camNum === 3) return page1.cam3_sn;
                                                if (camNum === 4) return page1.cam4_sn;
                                                if (camNum === 5) return page1.cam5_sn;
                                                if (camNum === 6) return page1.cam6_sn;
                                                if (camNum === 7) return page1.cam7_sn;
                                                if (camNum === 8) return page1.cam8_sn;
                                                return "";
                                            }
                                            onTextChanged: {
                                                // Update the corresponding property when text changes
                                                let camNum = parent.mappedIndex + 1;
                                                if (camNum === 1) page1.cam1_sn = text;
                                                if (camNum === 2) page1.cam2_sn = text;
                                                if (camNum === 3) page1.cam3_sn = text;
                                                if (camNum === 4) page1.cam4_sn = text;
                                                if (camNum === 5) page1.cam5_sn = text;
                                                if (camNum === 6) page1.cam6_sn = text;
                                                if (camNum === 7) page1.cam7_sn = text;
                                                if (camNum === 8) page1.cam8_sn = text;
                                            }
                                            background: Rectangle {
                                                radius: 4
                                                color: "#2C3E50"
                                                border.color: "#3E4E6F"
                                            }
                                        }
                                    }
                                }
                            }

                            // Spacer below table
                            Item {
                                Layout.columnSpan: 5
                                height: 10
                            }


                            // Controls row: Camera select/Test on left, Power buttons on right
                            RowLayout {
                                Layout.columnSpan: 5
                                spacing: 20

                                // Camera selection + test column
                                ColumnLayout {
                                    spacing: 10
                                    Layout.alignment: Qt.AlignLeft

                                    // Camera selector dropdown
                            ComboBox {
                                id: cameraDropdown
                                        Layout.preferredWidth: 248
                                Layout.preferredHeight: 40
                                        Layout.alignment: Qt.AlignLeft
                                model: ["Camera 1", "Camera 2", "Camera 3", "Camera 4", "Camera 5", "Camera 6", "Camera 7", "Camera 8", "All Cameras"]
                                currentIndex: 8  // Default to "All Cameras"
                                enabled: {
                                    if (sensorSelector.currentIndex === 0) {
                                        return MOTIONInterface.leftSensorConnected
                                    } else {
                                        return MOTIONInterface.rightSensorConnected
                                    }
                                }

                                onActivated: {
                                    var selectedIndex = cameraDropdown.currentIndex;
                                    switch (selectedIndex) {
                                        case 0: 
                                        case 1: 
                                        case 2: 
                                        case 3: 
                                        case 4: 
                                        case 5: 
                                        case 6: 
                                        case 7:
                                            break; 
                                        default:
                                            // console.log("All Cameras");
                                            break;
                                    }
                                }
                            }

                                    // Flash button
                                    Button {
                                id: testCameraButton
                                        text: "Flash"
                                        Layout.preferredWidth: 248
                                        Layout.preferredHeight: 40
                                        Layout.alignment: Qt.AlignLeft
                                        hoverEnabled: true
                                        enabled: {
                                            if (sensorSelector.currentIndex === 0) {
                                                return MOTIONInterface.leftSensorConnected
                                            } else {
                                                return MOTIONInterface.rightSensorConnected
                                            }
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            color: {
                                                if (!parent.enabled) {
                                                    return "#3A3F4B"
                                                }
                                                return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                            }
                                            radius: 4
                                            border.color: {
                                                if (!parent.enabled) {
                                                    return "#7F8C8D"
                                                }
                                                return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                            }
                                        }
                                        onClicked: {
                                    let selectedIndex = cameraDropdown.currentIndex;
                                    let cameraMask = 0x01 << selectedIndex;
                                    if (selectedIndex === 8) {
                                        cameraMask = 0xFF;  // All Cameras
                                    }
                                    let sensor_tag = "left";
                                    (sensorSelector.currentIndex === 0) ? sensor_tag = "left": sensor_tag = "right";
                                    // console.log("Test Camera Mask: " + cameraMask.toString(16));
                                    if(cameraMask == 0xFF){
                                        MOTIONInterface.configureAllCameras(sensor_tag);
                                    }else{
                                        MOTIONInterface.configureCamera(sensor_tag, cameraMask);
                                    }
                                        }
                                    }

                                    // NVCM permanent flash
                                    Button {
                                        id: nvcmFlashButton
                                        text: "Flash (permanent)"
                                        Layout.preferredWidth: 248
                                        Layout.preferredHeight: 40
                                        Layout.alignment: Qt.AlignLeft
                                        hoverEnabled: true
                                        enabled: {
                                            if (MOTIONInterface.nvcmFlashBusy) return false
                                            if (sensorSelector.currentIndex === 0) {
                                                return MOTIONInterface.leftSensorConnected
                                            } else {
                                                return MOTIONInterface.rightSensorConnected
                                            }
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            color: parent.enabled ? "#E74C3C" : "#7F8C8D"
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            color: parent.hovered && parent.enabled ? "#5A3A3A" : "#3A3F4B"
                                            radius: 4
                                            border.color: parent.enabled ? "#E74C3C" : "#7F8C8D"
                                        }
                                        onClicked: {
                                            let selectedIndex = cameraDropdown.currentIndex;
                                            let cameraMask = 0x01 << selectedIndex;
                                            let label = "Camera " + (selectedIndex + 1);
                                            if (selectedIndex === 8) {
                                                cameraMask = 0xFF;
                                                label = "ALL cameras (1-8)";
                                            }
                                            nvcmConfirmDialog.sensorTag =
                                                (sensorSelector.currentIndex === 0) ? "left" : "right";
                                            nvcmConfirmDialog.cameraMask = cameraMask;
                                            nvcmConfirmDialog.cameraLabel = label;
                                            nvcmConfirmDialog.open();
                                        }
                                    }

                                    // NVCM progress line
                                    Text {
                                        id: nvcmProgressText
                                        visible: MOTIONInterface.nvcmFlashBusy
                                        Layout.preferredWidth: 248
                                        color: "#F39C12"
                                        font.pixelSize: 12
                                        wrapMode: Text.WordWrap
                                        text: ""
                                    }

                                    Connections {
                                        target: MOTIONInterface
                                        function onNvcmFlashProgress(percent, message) {
                                            nvcmProgressText.text = message
                                        }
                                        function onNvcmFlashFinished(ok, summary) {
                                            nvcmProgressText.text = ""
                                            nvcmSummaryDialog.resultOk = ok
                                            nvcmSummaryDialog.summaryText = summary
                                            nvcmSummaryDialog.open()
                                        }
                                    }

                                    // Capture buttons row
                                    RowLayout {
                                        spacing: 8
                                        Layout.alignment: Qt.AlignLeft

                                    Button {
                                        id: captureHistogramButton
                                        text: "Capture"
                                        Layout.preferredWidth: 120
                                        Layout.preferredHeight: 40
                                        hoverEnabled: true
                                        enabled: {
                                            if (sensorSelector.currentIndex === 0) {
                                                return MOTIONInterface.leftSensorConnected
                                            } else {
                                                return MOTIONInterface.rightSensorConnected
                                            }
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            color: {
                                                if (!parent.enabled) {
                                                    return "#3A3F4B"
                                                }
                                                return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                            }
                                            radius: 4
                                            border.color: {
                                                if (!parent.enabled) {
                                                    return "#7F8C8D"
                                                }
                                                return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                            }
                                        }
                                        onClicked: {
                                            let selectedIndex = cameraDropdown.currentIndex;
                                            let sensor_tag = "left";
                                            (sensorSelector.currentIndex === 0) ? sensor_tag = "left": sensor_tag = "right";
                                            
                                            if (selectedIndex < 8) {
                                                // Single camera - get its serial number from the properties
                                                let serialNumber = "";
                                                let camNum = selectedIndex + 1;
                                                
                                                // Get the serial number from the appropriate property
                                                if (camNum === 1) serialNumber = page1.cam1_sn;
                                                else if (camNum === 2) serialNumber = page1.cam2_sn;
                                                else if (camNum === 3) serialNumber = page1.cam3_sn;
                                                else if (camNum === 4) serialNumber = page1.cam4_sn;
                                                else if (camNum === 5) serialNumber = page1.cam5_sn;
                                                else if (camNum === 6) serialNumber = page1.cam6_sn;
                                                else if (camNum === 7) serialNumber = page1.cam7_sn;
                                                else if (camNum === 8) serialNumber = page1.cam8_sn;
                                                
                                                // Use camera number as fallback if serial number is empty
                                                if (serialNumber === "") {
                                                    serialNumber = camNum.toString();
                                                }
                                                
                                                // console.log("Capturing histogram for camera", selectedIndex, "with SN", serialNumber);
                                                    MOTIONInterface.captureHistogramToCSV(sensor_tag, selectedIndex, serialNumber, false);
                                            } else {
                                                // All cameras - capture each individually with their serial numbers
                                                // console.log("Capturing histograms for all cameras with individual serial numbers");
                                                
                                                // Collect all serial numbers in display order
                                                let serialNumbers = [];
                                                // Map display order: [0,7,1,6,2,5,3,4] corresponds to cameras [1,8,2,7,3,6,4,5]
                                                let displayOrder = [0, 7, 1, 6, 2, 5, 3, 4];
                                                for (let i = 0; i < 8; i++) {
                                                    let camNum = displayOrder[i] + 1;
                                                    let serialNumber = "";
                                                    if (camNum === 1) serialNumber = page1.cam1_sn;
                                                    else if (camNum === 2) serialNumber = page1.cam2_sn;
                                                    else if (camNum === 3) serialNumber = page1.cam3_sn;
                                                    else if (camNum === 4) serialNumber = page1.cam4_sn;
                                                    else if (camNum === 5) serialNumber = page1.cam5_sn;
                                                    else if (camNum === 6) serialNumber = page1.cam6_sn;
                                                    else if (camNum === 7) serialNumber = page1.cam7_sn;
                                                    else if (camNum === 8) serialNumber = page1.cam8_sn;
                                                    
                                                    // Use camera number as fallback if serial number is empty
                                                    if (serialNumber === "") {
                                                        serialNumber = camNum.toString();
                                                    }
                                                    serialNumbers.push(serialNumber);
                                                }
                                                
                                                MOTIONInterface.captureAllCamerasHistogramToCSV(sensor_tag, false, serialNumbers);
                                                }
                                            }
                                        }

                                        Button {
                                            id: captureDarkButton
                                            text: "Capture (Dark)"
                                            Layout.preferredWidth: 120
                                Layout.preferredHeight: 40
                                            hoverEnabled: true
                                enabled: {
                                    if (sensorSelector.currentIndex === 0) {
                                        return MOTIONInterface.leftSensorConnected
                                    } else {
                                        return MOTIONInterface.rightSensorConnected
                                    }
                                }
                                            contentItem: Text {
                                                text: parent.text
                                                color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                                horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter
                                            }
                                            background: Rectangle {
                                                color: {
                                                    if (!parent.enabled) {
                                                        return "#3A3F4B"
                                                    }
                                                    return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                                }
                                                radius: 4
                                                border.color: {
                                                    if (!parent.enabled) {
                                                        return "#7F8C8D"
                                                    }
                                                    return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                                }
                                            }
                                            onClicked: {
                                                let selectedIndex = cameraDropdown.currentIndex;
                                                let sensor_tag = "left";
                                                (sensorSelector.currentIndex === 0) ? sensor_tag = "left": sensor_tag = "right";
                                                
                                                if (selectedIndex < 8) {
                                                    // Single camera - get its serial number from the properties
                                                    let serialNumber = "";
                                                    let camNum = selectedIndex + 1;
                                                    
                                                    // Get the serial number from the appropriate property
                                                    if (camNum === 1) serialNumber = page1.cam1_sn;
                                                    else if (camNum === 2) serialNumber = page1.cam2_sn;
                                                    else if (camNum === 3) serialNumber = page1.cam3_sn;
                                                    else if (camNum === 4) serialNumber = page1.cam4_sn;
                                                    else if (camNum === 5) serialNumber = page1.cam5_sn;
                                                    else if (camNum === 6) serialNumber = page1.cam6_sn;
                                                    else if (camNum === 7) serialNumber = page1.cam7_sn;
                                                    else if (camNum === 8) serialNumber = page1.cam8_sn;
                                                    
                                                    // Use camera number as fallback if serial number is empty
                                                    if (serialNumber === "") {
                                                        serialNumber = camNum.toString();
                                                    }
                                                    
                                                // console.log("Capturing dark histogram for camera", selectedIndex, "with SN", serialNumber);
                                                MOTIONInterface.captureHistogramToCSV(sensor_tag, selectedIndex, serialNumber, true);
                                                } else {
                                                    // All cameras - capture each individually with their serial numbers
                                                // console.log("Capturing dark histograms for all cameras with individual serial numbers");
                                                
                                                // Collect all serial numbers in display order
                                                let serialNumbers = [];
                                                // Map display order: [0,7,1,6,2,5,3,4] corresponds to cameras [1,8,2,7,3,6,4,5]
                                                let displayOrder = [0, 7, 1, 6, 2, 5, 3, 4];
                                                for (let i = 0; i < 8; i++) {
                                                    let camNum = displayOrder[i] + 1;
                                                    let serialNumber = "";
                                                    if (camNum === 1) serialNumber = page1.cam1_sn;
                                                    else if (camNum === 2) serialNumber = page1.cam2_sn;
                                                    else if (camNum === 3) serialNumber = page1.cam3_sn;
                                                    else if (camNum === 4) serialNumber = page1.cam4_sn;
                                                    else if (camNum === 5) serialNumber = page1.cam5_sn;
                                                    else if (camNum === 6) serialNumber = page1.cam6_sn;
                                                    else if (camNum === 7) serialNumber = page1.cam7_sn;
                                                    else if (camNum === 8) serialNumber = page1.cam8_sn;
                                                    
                                                    // Use camera number as fallback if serial number is empty
                                                    if (serialNumber === "") {
                                                        serialNumber = camNum.toString();
                                                    }
                                                    serialNumbers.push(serialNumber);
                                                }
                                                
                                                MOTIONInterface.captureAllCamerasHistogramToCSV(sensor_tag, true, serialNumbers);
                                                }
                                            }
                                        }
                                    }
                                }

                                // Spacer to push power buttons to the right
                                Item {
                                    Layout.fillWidth: true
                                }

                                // Power buttons column
                                ColumnLayout {
                                    spacing: 8
                                    Layout.alignment: Qt.AlignBottom | Qt.AlignRight


                                    // Power On button with status indicator
                                    RowLayout {
                                        spacing: 8
                                        Layout.alignment: Qt.AlignHCenter
                                        
                                        Rectangle {
                                            width: 16
                                            height: 16
                                            radius: 8
                                            color: {
                                                // Check if all cameras are powered
                                                const allPowered = page1.camera1_powered && page1.camera2_powered && 
                                                                   page1.camera3_powered && page1.camera4_powered && 
                                                                   page1.camera5_powered && page1.camera6_powered && 
                                                                   page1.camera7_powered && page1.camera8_powered;
                                                return allPowered ? "#2ECC71" : "#E74C3C"; // Green if all on, Red if any off
                                            }
                                            border.color: "#BDC3C7"
                                            border.width: 1
                                        }

                            Button {
                                        id: camPowerOnBtn
                                        text: "Power Cameras On"
                                        Layout.preferredWidth: 160
                                        Layout.preferredHeight: 40
                                        hoverEnabled: true
                                enabled: {
                                    if (sensorSelector.currentIndex === 0) {
                                        return MOTIONInterface.leftSensorConnected
                                    } else {
                                        return MOTIONInterface.rightSensorConnected
                                    }
                                }
                                contentItem: Text {
                                    text: parent.text
                                            color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    color: {
                                        if (!parent.enabled) {
                                                    return "#3A3F4B"
                                        }
                                                return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                                    return "#7F8C8D"
                                        }
                                                return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                    }
                                }
                                onClicked: {
                                            let target = "left";
                                            (sensorSelector.currentIndex === 0) ? target = "left": target = "right";
                                            MOTIONInterface.powerCamerasOn(target)
                                            
                                            // Automatically query power status after powering on
                                            let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                                            MOTIONInterface.queryCameraPowerStatus(sensor_tag)
                                        }
                                    }
                                    }

                                    // Power Off button with spacer to align with Power On button
                                    RowLayout {
                                        spacing: 8
                                        Layout.alignment: Qt.AlignHCenter
                                        
                                        // Spacer to match the circle width from Power On button
                                        Item {
                                            width: 16
                                            height: 16
                                        }

                                    Button {
                                        id: camPowerOffBtn
                                        text: "Power Cameras Off"
                                        Layout.preferredWidth: 160
                                        Layout.preferredHeight: 40
                                        hoverEnabled: true
                                        enabled: {
                                            if (sensorSelector.currentIndex === 0) {
                                                return MOTIONInterface.leftSensorConnected
                                            } else {
                                                return MOTIONInterface.rightSensorConnected
                                            }
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            color: {
                                                if (!parent.enabled) {
                                                    return "#3A3F4B"
                                                }
                                                return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                            }
                                            radius: 4
                                            border.color: {
                                                if (!parent.enabled) {
                                                    return "#7F8C8D"
                                                }
                                                return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                            }
                                        }
                                        onClicked: {
                                            let target = "left";
                                            (sensorSelector.currentIndex === 0) ? target = "left": target = "right";
                                            MOTIONInterface.powerCamerasOff(target)
                                            
                                            // Automatically query power status after powering off
                                            let sensor_tag = (sensorSelector.currentIndex === 0) ? "left" : "right";
                                            MOTIONInterface.queryCameraPowerStatus(sensor_tag)
                                        }
                                    }
                                    }
                                }
                            }
                            
                            // CSV Output Directory Controls (below capture buttons)
                            RowLayout {
                                Layout.columnSpan: 5
                                spacing: 10
                                Layout.alignment: Qt.AlignLeft
                                Layout.topMargin: 15

                                Text {
                                    text: "CSV Output:"
                                    color: "#BDC3C7"
                                    font.pixelSize: 14
                                }

                                Text {
                                    id: csvOutputPathText
                                    text: MOTIONInterface.csvOutputDirectory
                                    color: "#3498DB"
                                    font.pixelSize: 12
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                    onTextChanged: {
                                        // Update text when directory changes
                                        text = MOTIONInterface.csvOutputDirectory
                                    }
                                }

                                Button {
                                    text: "Change Directory"
                                    Layout.preferredWidth: 140
                                    Layout.preferredHeight: 30
                                    hoverEnabled: true
                                    contentItem: Text {
                                        text: parent.text
                                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        font.pixelSize: 12
                                    }
                                    background: Rectangle {
                                        color: {
                                            if (!parent.enabled) {
                                                return "#3A3F4B"
                                            }
                                            return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                        }
                                        radius: 4
                                        border.color: {
                                            if (!parent.enabled) {
                                                return "#7F8C8D"
                                            }
                                            return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                        }
                                    }
                                    onClicked: {
                                        csvFolderDialog.open()
                                    }
                                }
                            }
                            
                        }

                    }                    
                }

                // Large Third Column
                Rectangle {
                    Layout.fillHeight: true
                    Layout.fillWidth: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 20
                        spacing: 10

                        // Sensor Status Indicator
                        RowLayout {
                            spacing: 8

                            Text { text: "Sensor"; font.pixelSize: 16; color: "#BDC3C7" }

                            // Sensor selection dropdown
                            ComboBox {
                                id: sensorSelector
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 28
                                model: ["Left", "Right"]
                                currentIndex: 0 // Default to Left

                                // Smaller font for the selected text
                                contentItem: Text {
                                    text: sensorSelector.displayText
                                    font.pixelSize: 12     // Change this for smaller text
                                    color: "#BDC3C7"
                                    verticalAlignment: Text.AlignVCenter
                                    horizontalAlignment: Text.AlignHCenter
                                    elide: Text.ElideRight
                                }

                                onCurrentIndexChanged: {
                                    // console.log("Sensor selection changed to:", currentText)

                                    // Clear status texts
                                    pingResult.text = ""
                                    echoResult.text = ""
                                    toggleLedResult.text = ""
                                    fanControlResult.text = ""

                                    // Clear sensor data
                                    firmwareVersion = "N/A"
                                    deviceId = "N/A"
                                    sensor_temperature = 0.0
                                    amb_temperature = 0.0
                                    accel_x = accel_y = accel_z = 0
                                    gyro_x = gyro_y = gyro_z = 0

                                    // Reset camera test table
                                    for (let i = 0; i < cameraStatusModel.count; i++) {
                                        cameraStatusModel.set(i, {
                                            label: "Camera " + (i + 1),
                                            status: "Not Tested",
                                            color: "gray"
                                        });
                                    }

                                    // Clear camera power status
                                    camera1_powered = false;
                                    camera2_powered = false;
                                    camera3_powered = false;
                                    camera4_powered = false;
                                    camera5_powered = false;
                                    camera6_powered = false;
                                    camera7_powered = false;
                                    camera8_powered = false;

                                    // Clear fan control status
                                    fanControlOn = false;

                                    // Fetch new sensor states
                                    updateStates()
                                }
                            }

                            // Connection LED (changes based on selection)
                            Rectangle {
                                width: 20
                                height: 20
                                radius: 10
                                color: {
                                    if (sensorSelector.currentIndex === 0) {
                                        return MOTIONInterface.leftSensorConnected ? "green" : "red"
                                    } else {
                                        return MOTIONInterface.rightSensorConnected ? "green" : "red"
                                    }
                                }
                                border.color: "black"
                                border.width: 1
                            }
                        
                            // Spacer to push the Refresh Button to the right
                            Item {
                                Layout.fillWidth: true
                            }

                            
                            // Refresh Button
                            Rectangle {
                                width: 30
                                height: 30
                                radius: 15
                                color: enabled ? "#2C3E50" : "#7F8C8D"  // Dim when disabled
                                Layout.alignment: Qt.AlignRight  
                                enabled: {
                                    if (sensorSelector.currentIndex === 0) {
                                        return MOTIONInterface.leftSensorConnected
                                    } else {
                                        return MOTIONInterface.rightSensorConnected
                                    }
                                }

                                // Icon Text
                                Text {
                                    text: "\u21BB"  // Unicode for the refresh icon
                                    anchors.centerIn: parent
                                    font.pixelSize: 20
                                    font.family: iconFont.name  // Use the loaded custom font
                                    color: enabled ? "white" : "#BDC3C7"  // Dim icon text when disabled
                                }

                                MouseArea {
                                    id: refreshMouseArea
                                    anchors.fill: parent
                                    enabled: parent.enabled  // MouseArea also disabled when button is disabled
                                    hoverEnabled: true

                                    onClicked: {
                                        // console.log("Manual Refresh Triggered")
                                        updateStates();
                                    }

                                    onEntered: if (parent.enabled) parent.color = "#34495E"  // Highlight only when enabled
                                    onExited: parent.color = enabled ? "#2C3E50" : "#7F8C8D"
                                }

                                // Tooltip
                                ToolTip.visible: refreshMouseArea.containsMouse
                                ToolTip.text: "Refresh"
                                ToolTip.delay: 400  // Optional: delay before tooltip shows
                            }
                        }

                        // Divider Line
                        Rectangle {
                            Layout.fillWidth: true
                            height: 2
                            color: "#3E4E6F"
                        }

                        // Display Device ID (Smaller Text)
                        RowLayout {
                            spacing: 8
                            Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: deviceId; color: "#3498DB"; font.pixelSize: 14 }
                        }

                        // Display Firmware Version (Smaller Text)
                        RowLayout {
                            spacing: 8
                            Text { text: "Firmware Version:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: firmwareVersion; color: "#2ECC71"; font.pixelSize: 14 }
                        }


                        ColumnLayout {
                            Layout.alignment: Qt.AlignHCenter 
                            spacing: 25  

                            // TEMP Widget
                            TemperatureWidget {
                                id: tempWidget1
                                temperature: sensor_temperature
                                tempName: "Sensor Temperature"
                                Layout.alignment: Qt.AlignHCenter
                            }

                            // IMU Widget
                            IMUWidget {
                                mode: "Accel"
                                imuLabel: "IMU Data"
                                xVal: accel_x
                                yVal: accel_y
                                zVal: accel_z
                            }
                        }


                        // Soft Reset Button
                        Rectangle {
                            Layout.fillWidth: true
                            height: 40
                            radius: 10
                            color: enabled ? "#E74C3C" : "#7F8C8D"  // Red when enabled, gray when disabled
                            enabled: {
                                    if (sensorSelector.currentIndex === 0) {
                                        return MOTIONInterface.leftSensorConnected
                                    } else {
                                        return MOTIONInterface.rightSensorConnected
                                    }
                            }
                            Text {
                                text: "Soft Reset"
                                anchors.centerIn: parent
                                color: parent.enabled ? "white" : "#BDC3C7"  // White when enabled, light gray when disabled
                                font.pixelSize: 18
                                font.weight: Font.Bold
                            }

                            MouseArea {
                                anchors.fill: parent
                                enabled: parent.enabled  // Disable MouseArea when the button is disabled
                                onClicked: {
                                    let sensor_tag = "left";
                                    (sensorSelector.currentIndex === 0) ? sensor_tag = "left": sensor_tag = "right";
                                    // console.log("Soft Reset Triggered")
                                    MOTIONInterface.softResetSensor(sensor_tag)
                                }

                                onEntered: {
                                    if (parent.enabled) {
                                        parent.color = "#C0392B"  // Darker red on hover (only when enabled)
                                    }
                                }
                                onExited: {
                                    if (parent.enabled) {
                                        parent.color = "#E74C3C"  // Restore original color (only when enabled)
                                    }
                                }
                            }

                            Behavior on color {
                                ColorAnimation { duration: 200 }
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
}
