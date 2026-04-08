import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import OpenMotion 1.0 

import "../components"

Rectangle {
    id: page1
    width: parent.width
    height: parent.height
    color: "#29292B" // Background color for Page 1
    radius: 20
    opacity: 0.95 // Slight transparency for the content area

    // Properties for dynamic data
    property string firmwareVersion: "N/A"
    property string deviceId: "N/A"
    property string boardRevId: "N/A"
    property string rgbState: "Off" // Add property for Indicator state
    property real temperature1: 0.0
    property real temperature2: 0.0
    property real temperature3: 0.0
    property int fan_speed: 0
    property int fan1Rpm: -1
    property int fan2Rpm: -1
    property int fan3Rpm: -1
    property var fn: null
    property int rawValue: 0 
    property int tecTripValue: 0 
    
    readonly property int dataSize: {
        if (fn && fn.data_size) {
            const match = fn.data_size.match(/^(\d+)B$/);
            return match ? parseInt(match[1]) : 8;
        }
        return 8;
    }
    
    readonly property string placeholderHex: {
        switch (dataSize) {
            case 8: return "0x00";
            case 16: return "0x0000";
            case 24: return "0x000000";
            case 32: return "0x00000000";
            default: return "0x00";
        }
    }

    readonly property var hexValidator: {
        switch (dataSize) {
            case 8: return /0x[0-9a-fA-F]{1,2}/;
            case 16: return /0x[0-9a-fA-F]{1,4}/;
            case 24: return /0x[0-9a-fA-F]{1,6}/;
            case 32: return /0x[0-9a-fA-F]{1,8}/;
            default: return /0x[0-9a-fA-F]{1,2}/;
        }
    }

    // Define the model for accessSelector
    ListModel {
        id: accessModeModel
    }

    function updateFpgaFunctionUI(index) {
        accessModeModel.clear()

        // Defensive check: valid index and model element
        if (index < 0 || !functionSelector.model || index >= functionSelector.model.length) {
            fn = null
            hexInput.text = ""
            return
        }

        fn = functionSelector.model[index]
        if (!fn || !fn.direction) {
            console.warn("Function data is invalid")
            hexInput.text = ""
            return
        }

        const dir = fn.direction

        if (dir === "RD") {
            accessModeModel.append({ text: "Read" })
        } else if (dir === "WR") {
            accessModeModel.append({ text: "Write" })
        } else if (dir === "RW") {
            accessModeModel.append({ text: "Read" })
            accessModeModel.append({ text: "Write" })
        }

        accessSelector.currentIndex = 0
        hexInput.text = ""
    }

    function updateStates() {
        // console.log("Console Updating all states...")
        MOTIONInterface.queryConsoleInfo()
        MOTIONInterface.queryRGBState() // Query Indicator state
        MOTIONInterface.readFanFeedback() // One-shot fan PWM feedback read
        MOTIONInterface.queryConsoleTemperature()
        MOTIONInterface.queryTecTripValue();
    }


    // Run refresh logic immediately on page load if Console is already connected
    Component.onCompleted: {
        if (MOTIONInterface.consoleConnected) {
            // console.log("Page Loaded - Console Already Connected. Fetching Info...")
            updateStates()
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

    Connections {
        target: MOTIONInterface

        // Handle Console Connected state
        function onConsoleConnectedChanged() {
            if (MOTIONInterface.consoleConnected) {
                infoTimer.start()          // One-time info fetch
            } else {
                // console.log("Console Disconnected - Clearing Data...")
                firmwareVersion = "N/A"
                deviceId = "N/A"
                boardRevId = "N/A"
                rgbState = "Off" // Indicator off
                fan_speed = 0
                fan1Rpm = -1
                fan2Rpm = -1
                fan3Rpm = -1
                temperature1 = 0.0
                temperature2 = 0.0
                temperature3 = 0.0
                
                pingResult.text = ""
                echoResult.text = ""
                toggleLedResult.text = ""
                pduResult.text = ""
                tecResult.text = ""
                seedResult.text = ""
                safetyResult.text = ""
                safety2Result.text = ""
                taResult.text = ""
            }
        }

        // Handle device info response
        function onConsoleDeviceInfoReceived(fwVersion, devId, boardId) {
            firmwareVersion = fwVersion
            deviceId = devId
            boardRevId = boardId
        }

        function onTriggerStateChanged(state) {
            triggerStatus.text = state ? "On" : "Off";
            triggerStatus.color = state ? "green" : "red";
        }

        function onRgbStateReceived(stateValue, stateText) {
            rgbState = stateText
            rgbLedResult.text = stateText  // Display the state as text
            rgbLedDropdown.currentIndex = stateValue  // Sync ComboBox to received state
        }

        function onFanFeedbackUpdated(fan1, fan2, fan3) {
            fan1Rpm = fan1
            fan2Rpm = fan2
            fan3Rpm = fan3
        }

        function onConsoleTemperatureUpdated(temp1, temp2, temp3) {
            temperature1 = temp1
            temperature2 = temp2
            temperature3 = temp3
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Title
        Text {
            text: "Console Module Unit Tests"
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
                        height: 310
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
                        GridLayout {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.leftMargin: 20   
                            anchors.topMargin: 40    
                            columns: 5
                            rowSpacing: 10
                            columnSpacing: 10

                            // Row 1
                            // Ping Button and Result
                            Button {
                                id: pingButton
                                text: "Ping"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: pingButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    if(MOTIONInterface.sendPingCommand("CONSOLE")){                                        
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

                            Item {
                                Layout.preferredWidth: 200 
                            }

                            Button {
                                id: ledButton
                                text: "Toggle LED"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: ledButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    if(MOTIONInterface.sendLedToggleCommand("CONSOLE"))
                                    {
                                        toggleLedResult.text = "LED Toggled"
                                        toggleLedResult.color = "green"
                                    }
                                    else{
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

                            // Row 2
                            // Echo Button and Result
                            Button {
                                id: echoButton
                                text: "Echo"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: echoButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {

                                    if(MOTIONInterface.sendEchoCommand("CONSOLE"))
                                    {
                                        echoResult.text = "Echo SUCCESS"
                                        echoResult.color = "green"
                                    }
                                    else{
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

                            Item {
                                Layout.preferredWidth: 200 
                            }

                            ComboBox {
                                id: rgbLedDropdown
                                Layout.preferredWidth: 120
                                Layout.preferredHeight: 28
                                model: ["Off", "IND1", "IND2", "IND3"]
                                enabled: MOTIONInterface.consoleConnected  

                                onActivated: {
                                    let rgbValue = rgbLedDropdown.currentIndex  // Directly map ComboBox index to integer value
                                    MOTIONInterface.setRGBState(rgbValue)         // Assuming you implement this new method
                                    rgbLedResult.text = rgbLedDropdown.currentText
                                }
                            }
                            Text {
                                id: rgbLedResult
                                Layout.preferredWidth: 80
                                color: "#BDC3C7"
                                text: "Off"
                            }

                            // Row 3
                            // PDU Button and Result
                            Button {
                                id: pduButton
                                text: "PDU"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: pduButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    var devices = MOTIONInterface.scanI2C(1, 0)
                                    if (devices && devices.includes("0x20") && devices.includes("0x48")  && devices.includes("0x4b")) {
                                        pduResult.text = "PDU SUCCESS"
                                        pduResult.color = "green"
                                    } else {
                                        pduResult.text = "PDU FAILED"
                                        pduResult.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: pduResult
                                Layout.preferredWidth: 80
                                text: ""
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.preferredWidth: 200 
                            }

                            Button {
                                id: seedButton
                                text: "Seed"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: seedButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    var devices = MOTIONInterface.scanI2C(1, 5)
                                    if (devices && devices.includes("0x41")) {
                                        seedResult.text = "Seed SUCCESS"
                                        seedResult.color = "green"
                                    } else {
                                        seedResult.text = "Seed FAILED"
                                        seedResult.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: seedResult
                                Layout.preferredWidth: 80
                                color: "#BDC3C7"
                                text: ""
                            }

                            // Row 4
                            // TA Button and Result
                            Button {
                                id: taButton
                                text: "TA"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: taButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    var devices = MOTIONInterface.scanI2C(1, 4)
                                    if (devices && devices.includes("0x41")) {
                                        taResult.text = "TA SUCCESS"
                                        taResult.color = "green"
                                    } else {
                                        taResult.text = "TA FAILED"
                                        taResult.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: taResult
                                Layout.preferredWidth: 80
                                text: ""
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.preferredWidth: 200 
                            }

                            Button {
                                id: safetyButton
                                text: "Safety EE"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: safetyButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    
                                    var devices = MOTIONInterface.scanI2C(1, 6)
                                    if (devices && devices.includes("0x41")) {
                                        safetyResult.text = "Safety EE SUCCESS"
                                        safetyResult.color = "green"
                                    } else {
                                        safetyResult.text = "Safety EE FAILED"
                                        safetyResult.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: safetyResult
                                Layout.preferredWidth: 80
                                color: "#BDC3C7"
                                text: ""
                            }

                            

                            // Row 5
                            // TEC Button and Result
                            Button {
                                id: tecButton
                                text: "TEC"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: tecButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    if (MOTIONInterface.getTecEnabled()) {
                                        tecResult.text = "TEC SUCCESS"
                                        tecResult.color = "green"
                                    } else {
                                        tecResult.text = "TEC FAILED"
                                        tecResult.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: tecResult
                                Layout.preferredWidth: 80
                                text: ""
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.preferredWidth: 200 
                            }

                            Button {
                                id: safety2Button
                                text: "Safety OPT"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 40
                                hoverEnabled: true  // Enable hover detection
                                enabled: MOTIONInterface.consoleConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: safety2ButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    
                                    var devices = MOTIONInterface.scanI2C(1, 7)
                                    if (devices && devices.includes("0x41")) {
                                        safety2Result.text = "Safety OPT SUCCESS"
                                        safety2Result.color = "green"
                                    } else {
                                        safety2Result.text = "Safety OPT FAILED"
                                        safety2Result.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: safety2Result
                                Layout.preferredWidth: 80
                                color: "#BDC3C7"
                                text: ""
                            }
                        }
                    }

                    // FPGA Utility
                    Rectangle {
                        width: 650
                        height: 140
                        radius: 8
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2
                        enabled: MOTIONInterface.consoleConnected

                        // Title
                        Text {
                            id: fpgaTitle
                            text: "FPGA I2C Utility"
                            color: "#BDC3C7"
                            font.pixelSize: 16
                            font.bold: true
                            anchors.top: parent.top
                            anchors.topMargin: 12
                            anchors.horizontalCenter: parent.horizontalCenter
                        }

                        ColumnLayout {
                            id: fpgaLayout
                            anchors.top: fpgaTitle.bottom
                            anchors.topMargin: 12
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.margins: 12
                            spacing: 10

                            // FPGA + Function Combo Row
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                ComboBox {
                                    id: fpgaSelector
                                    model: MOTIONInterface.fpgaAddressModel
                                    textRole: "label"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32

                                    onCurrentIndexChanged: {
                                        accessModeModel.clear()
                                        functionSelector.currentIndex = 0;
                                        updateFpgaFunctionUI(0)
                                    }
                                }

                                ComboBox {
                                    id: functionSelector
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    model: fpgaSelector.currentIndex >= 0 ? MOTIONInterface.fpgaAddressModel[fpgaSelector.currentIndex].functions : []
                                    textRole: "name"
                                    enabled: fpgaSelector.currentIndex >= 0

                                    onCurrentIndexChanged: updateFpgaFunctionUI(currentIndex)
                                    onModelChanged: {
                                        if (functionSelector.model.length > 0) {
                                            functionSelector.currentIndex = 0;
                                            updateFpgaFunctionUI(0);
                                        }
                                    }
                                }
                            }

                            // Access + Input + Execute Row
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                ComboBox {
                                    id: accessSelector
                                    Layout.preferredWidth: 100
                                    Layout.preferredHeight: 32
                                    model: accessModeModel
                                    textRole: "text"
                                }

                                DoubleValidator {
                                    id: doubleVal
                                    bottom: 0
                                }

                                RegularExpressionValidator {
                                    id: hexVal
                                    regularExpression: hexValidator
                                }

                                TextField {
                                    id: hexInput
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    placeholderText: fn && fn.unit ? `e.g. 12.8 ${fn.unit}` : placeholderHex
                                    enabled: accessSelector.currentText === "Write"
                                    validator: fn && fn.unit ? doubleVal : hexVal
                                    text: {
                                        if (!fn || rawValue === undefined) return "";
                                        if (fn.unit && fn.scale)
                                            return (rawValue * fn.scale).toFixed(2);
                                        return "0x" + rawValue.toString(16).toUpperCase();
                                    }
                                }

                                Button {
                                    id: exeButton
                                    text: "Execute"
                                    Layout.preferredWidth: 100
                                    Layout.preferredHeight: 40
                                    hoverEnabled: true
                                    enabled: MOTIONInterface.consoleConnected && functionSelector.currentIndex >= 0 &&
                                            (accessSelector.currentText === "Read" || (hexInput.acceptableInput && hexInput.text.length > 0))

                                    contentItem: Text {
                                        text: parent.text
                                        color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    background: Rectangle {
                                        color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                                        border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                        radius: 4
                                    }

                                    onClicked: {
                                        const fpga = MOTIONInterface.fpgaAddressModel[fpgaSelector.currentIndex];
                                        const i2cAddr = fpga.i2c_addr;
                                        const muxIdx = fpga.mux_idx;
                                        const channel = fpga.channel;
                                        const isMsbFirst = fpga.isMsbFirst;

                                        const fn = functionSelector.model[functionSelector.currentIndex];
                                        const offset = fn.start_address;
                                        const dir = accessSelector.currentText;
                                        const length = parseInt(fn.data_size.replace("B", "")) / 8;
                                        let data = hexInput.text;

                                        if (dir === "Read") {
                                            // console.log(`READ from ${fpga.label} @ 0x${offset.toString(16)}`);
                                            let result = MOTIONInterface.i2cReadBytes("CONSOLE", muxIdx, channel, i2cAddr, offset, length);

                                            if (result.length === 0) {
                                                console.error("Read failed or returned empty array.");
                                                i2cStatus.text = "Read failed";
                                                i2cStatus.color = "red";
                                            } else {
                                                let fullValue = 0;
    
                                                if (isMsbFirst) {
                                                    // MSB first (big-endian, default)
                                                    for (let i = 0; i < result.length; i++) {
                                                        fullValue = (fullValue << 8) | result[i];
                                                    }
                                                } else {
                                                    // LSB first (little-endian, reversed)
                                                    for (let i = result.length - 1; i >= 0; i--) {
                                                        fullValue = (fullValue << 8) | result[i];
                                                    }
                                                }

                                                rawValue = fullValue;  // store globally

                                                if (fn.unit && fn.scale) {
                                                    hexInput.text = (fullValue * fn.scale).toFixed(2);
                                                } else {
                                                    let hexStr = "0x" + fullValue.toString(16).toUpperCase().padStart(length * 2, "0");
                                                    hexInput.text = hexStr;
                                                }

                                                // console.log("Read success:", hexInput.text);
                                                i2cStatus.text = "Read successful";
                                                i2cStatus.color = "lightgreen";
                                            }

                                            cleari2cStatusTimer.start();
                                        } else {
                                            // console.log(`WRITE to ${fpga.label} @ 0x${offset.toString(16)} = ${data}`);

                                            let fullValue = 0;

                                            if (fn.unit && fn.scale) {
                                                const floatVal = parseFloat(data);
                                                if (isNaN(floatVal)) {
                                                    console.warn("Invalid numeric input for unit conversion.");
                                                    return;
                                                }
                                                fullValue = Math.round(floatVal / fn.scale);
                                            } else {
                                                let sanitized = data.replace(/0x/gi, "").replace(/\s+/g, "");

                                                if (sanitized.length > length * 2) {
                                                    console.warn("Input too long, trimming.");
                                                    sanitized = sanitized.slice(-length * 2);
                                                } else if (sanitized.length < length * 2) {
                                                    sanitized = sanitized.padStart(length * 2, "0");
                                                }

                                                fullValue = parseInt(sanitized, 16);
                                            }

                                            rawValue = fullValue;  // store globally

                                            let dataToSend = [];
    
                                            if (isMsbFirst) {
                                                // MSB first (big-endian, default)
                                                for (let i = length - 1; i >= 0; i--) {
                                                    dataToSend.push((fullValue >> (i * 8)) & 0xFF);
                                                }
                                            } else {
                                                // LSB first (little-endian, reversed)
                                                for (let i = 0; i < length; i++) {
                                                    dataToSend.push((fullValue >> (i * 8)) & 0xFF);
                                                }
                                            }

                                            // console.log("Data to send:", dataToSend.map(b => "0x" + b.toString(16).padStart(2, "0")).join(" "));

                                            let success = MOTIONInterface.i2cWriteBytes("CONSOLE", muxIdx, channel, i2cAddr, offset, dataToSend);

                                            if (success) {
                                                // console.log("Write successful.");
                                                i2cStatus.text = "Write successful";
                                                i2cStatus.color = "lightgreen";
                                            } else {
                                                console.error("Write failed.");
                                                i2cStatus.text = "Write failed";
                                                i2cStatus.color = "red";
                                            }

                                            cleari2cStatusTimer.start();
                                        }
                                    }
                                }
                            }

                            Text {
                                id: i2cStatus
                                text: ""
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                            }

                            Timer {
                                id: cleari2cStatusTimer
                                interval: 2000
                                running: false
                                repeat: false
                                onTriggered: i2cStatus.text = ""
                            }
                        }


                    }

                    // Fan Tests and TA Gain Row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        // Fan Tests Box (left half)
                        Rectangle {
                            id: fanTestsBox
                            Layout.preferredWidth: 320
                            height: 148
                            radius: 8
                            color: "#1E1E20"
                            border.color: "#3E4E6F"
                            border.width: 2

                            Text {
                                text: "Fan Tests"
                                color: "#BDC3C7"
                                font.pixelSize: 16
                                anchors.top: parent.top
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.topMargin: 3
                            }

                            Column {
                                anchors.top: parent.top
                                anchors.topMargin: 22
                                anchors.horizontalCenter: parent.horizontalCenter
                                spacing: 2

                                Text {
                                    text: "Console Fan: " + (fanSlider.value === 0 ? "OFF" : fanSlider.value.toFixed(0) + "%")
                                    color: "#BDC3C7"
                                    font.pixelSize: 13
                                }

                                Slider {
                                    id: fanSlider
                                    width: 280
                                    height: 22
                                    from: 0
                                    to: 100
                                    stepSize: 10
                                    value: fan_speed || 0
                                    enabled: MOTIONInterface.consoleConnected

                                    property bool userIsSliding: false

                                    onPressedChanged: {
                                        if (pressed) {
                                            userIsSliding = true
                                        } else if (!pressed && userIsSliding) {
                                            let snappedValue = Math.round(value / 10) * 10
                                            value = snappedValue
                                            userIsSliding = false
                                            let success = MOTIONInterface.setFanLevel(snappedValue)
                                            if (!success) console.error("Failed to set fan speed")
                                        }
                                    }
                                }

                                Rectangle { width: 280; height: 1; color: "#3E4E6F" }

                                Text {
                                    width: 280
                                    horizontalAlignment: Text.AlignHCenter
                                    text: "1: " + (fan1Rpm < 0 ? "--" : fan1Rpm + " RPM") +
                                          "  2: " + (fan2Rpm < 0 ? "--" : fan2Rpm + " RPM") +
                                          "  3: " + (fan3Rpm < 0 ? "--" : fan3Rpm + " RPM")
                                    color: "#2ECC71"
                                    font.pixelSize: 13
                                    font.weight: Font.Bold
                                }

                                Button {
                                    text: "Get Fan Feedback"
                                    enabled: MOTIONInterface.consoleConnected
                                    width: 280
                                    height: 32
                                    font.pixelSize: 12
                                    onClicked: MOTIONInterface.readFanFeedback()
                                }
                            }
                        }

                        // Console User Configuration (right half)
                        Rectangle {
                            id: tecTripBox
                            Layout.preferredWidth: 320
                            height: 148
                            radius: 8
                            color: "#1E1E20"
                            border.color: "#3E4E6F"
                            border.width: 2
                            enabled: MOTIONInterface.consoleConnected

                            // Title
                            Text {
                                text: "User Config Values"
                                color: "#BDC3C7"
                                font.pixelSize: 18
                                anchors.top: parent.top
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.topMargin: 5
                                visible: false
                            }

                            Column {
                                anchors.top: parent.top
                                anchors.topMargin: 34
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.margins: 12
                                spacing: 8
                                visible: false

                                RowLayout {
                                    spacing: 8
                                    Layout.fillWidth: true

                                    Text {
                                        text: "TEC_TRIP:" 
                                        color: "#BDC3C7"
                                        font.pixelSize: 14
                                        Layout.alignment: Qt.AlignVCenter
                                    }

                                    IntValidator {
                                        id: taIntVal
                                        bottom: 0
                                        top: 125
                                    }

                                    TextField {
                                        id: tecTripInput
                                        Layout.preferredWidth: 80
                                        Layout.preferredHeight: 40
                                        placeholderText: "0-125"
                                        validator: taIntVal
                                        inputMethodHints: Qt.ImhDigitsOnly
                                        text: MOTIONInterface.tecTripValue.toString()

                                        onAccepted: {
                                            // Clamp and normalize
                                            let v = parseInt(text)
                                            if (isNaN(v)) {
                                                text = ""
                                            } else {
                                                if (v < 0) v = 0
                                                if (v > 125) v = 125
                                                text = v.toString()
                                                let ok = MOTIONInterface.setTecTrip(v)
                                                if (!ok) {
                                                  
                                                }
                                            }
                                        }

                                        property string tecTripSetError: ""
                                        Timer {
                                            id: tecTripSetErrorTimer
                                            interval: 2500
                                            running: false
                                            repeat: false
                                            onTriggered: tecTripInput.tecTripSetError = ""
                                        }
                                        Connections {
                                            target: MOTIONInterface
                                            function onTecTripValueChanged() {
                                                tecTripInput.text = MOTIONInterface.tecTripValue.toString()
                                            }
                                            function onTaGainSetFailed(msg) {
                                                tecTripInput.text = MOTIONInterface.tecTripValue.toString()
                                                tecTripInput.tecTripSetError = msg
                                                tecTripSetErrorTimer.restart()
                                            }
                                        }

                                        Rectangle {
                                            anchors.top: tecTripInput.bottom
                                            anchors.left: tecTripInput.left
                                            width: tecTripInput.width
                                            height: tecTripInput.tecTripSetError ? 20 : 0
                                            color: "transparent"
                                            visible: tecTripInput.tecTripSetError.length > 0
                                            Text {
                                                anchors.fill: parent
                                                text: tecTripInput.tecTripSetError
                                                color: "red"
                                                font.pixelSize: 12
                                                verticalAlignment: Text.AlignVCenter
                                                horizontalAlignment: Text.AlignLeft
                                            }
                                        }
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

                        // Console Status Indicator
                        RowLayout {
                            spacing: 8

                            Text { text: "Console"; font.pixelSize: 16; color: "#BDC3C7" }
                        
                            Rectangle {
                                width: 20
                                height: 20
                                radius: 10
                                color: MOTIONInterface.consoleConnected ? "green" : "red"
                                border.color: "black"
                                border.width: 1
                            }

                            Text {
                                text: MOTIONInterface.consoleConnected ? "Connected" : "Not Connected"
                                font.pixelSize: 16
                                color: "#BDC3C7"
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
                                enabled: MOTIONInterface.consoleConnected

                                // Icon Text
                                Text {
                                    text: "\u21BB"  // Unicode for the refresh icon
                                    anchors.centerIn: parent
                                    font.pixelSize: 20
                                    font.family: iconFont.name  // Use the loaded custom font
                                    color: enabled ? "white" : "#BDC3C7"  // Dim icon text when disabled
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: parent.enabled  // MouseArea also disabled when button is disabled
                                    onClicked: {
                                        // console.log("Manual Refresh Triggered")
                                        updateStates();
                                    }

                                    onEntered: if (parent.enabled) parent.color = "#34495E"  // Highlight only when enabled
                                    onExited: parent.color = enabled ? "#2C3E50" : "#7F8C8D"
                                }
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

                        // Board Rev ID (Smaller Text)
                        RowLayout {
                            spacing: 8
                            Text { text: "Board Rev ID:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: boardRevId; color: "#3498DB"; font.pixelSize: 14 }
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

                            // TEMP #1 Widget
                            MiniTemperatureWidget {
                                id: tempWidget1
                                temperature: temperature1
                                tempName: "MCU Temp"
                                Layout.alignment: Qt.AlignHCenter
                            }

                            // TEMP #2 Widget
                            MiniTemperatureWidget {
                                id: tempWidget2
                                temperature: temperature2
                                tempName: "Safety Temp"
                                Layout.alignment: Qt.AlignHCenter
                            }

                            // TEMP #3 Widget
                            MiniTemperatureWidget {
                                id: tempWidget3
                                temperature: temperature3
                                tempName: "TA Temp"
                                Layout.alignment: Qt.AlignHCenter
                            }
                        }

                        // Soft Reset Button
                        Rectangle {
                            Layout.fillWidth: true
                            height: 40
                            radius: 10
                            color: enabled ? "#E74C3C" : "#7F8C8D"  // Red when enabled, gray when disabled
                            enabled: MOTIONInterface.consoleConnected

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
                                    // console.log("Soft Reset Triggered")
                                    MOTIONInterface.softResetSensor("CONSOLE")
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
