/*!
 * ASP.NET SignalR JavaScript Library 2.4.2
 * http://signalr.net/
 *
 * Copyright (c) .NET Foundation. All rights reserved.
 * Licensed under the Apache License, Version 2.0. See License.txt in the project root for license information.
 *
 */

/// <reference path="..\..\SignalR.Client.JS\Scripts\jquery-1.6.4.js" />
/// <reference path="jquery.signalR.js" />
(function ($, window, undefined) {
    /// <param name="$" type="jQuery" />
    "use strict";

    if (typeof ($.signalR) !== "function") {
        throw new Error("SignalR: SignalR is not loaded. Please ensure jquery.signalR-x.js is referenced before ~/signalr/js.");
    }

    var signalR = $.signalR;

    function makeProxyCallback(hub, callback) {
        return function () {
            // Call the client hub method
            callback.apply(hub, $.makeArray(arguments));
        };
    }

    function registerHubProxies(instance, shouldSubscribe) {
        var key, hub, memberKey, memberValue, subscriptionMethod;

        for (key in instance) {
            if (instance.hasOwnProperty(key)) {
                hub = instance[key];

                if (!(hub.hubName)) {
                    // Not a client hub
                    continue;
                }

                if (shouldSubscribe) {
                    // We want to subscribe to the hub events
                    subscriptionMethod = hub.on;
                } else {
                    // We want to unsubscribe from the hub events
                    subscriptionMethod = hub.off;
                }

                // Loop through all members on the hub and find client hub functions to subscribe/unsubscribe
                for (memberKey in hub.client) {
                    if (hub.client.hasOwnProperty(memberKey)) {
                        memberValue = hub.client[memberKey];

                        if (!$.isFunction(memberValue)) {
                            // Not a client hub function
                            continue;
                        }

                        // Use the actual user-provided callback as the "identity" value for the registration.
                        subscriptionMethod.call(hub, memberKey, makeProxyCallback(hub, memberValue), memberValue);
                    }
                }
            }
        }
    }

    $.hubConnection.prototype.createHubProxies = function () {
        var proxies = {};
        this.starting(function () {
            // Register the hub proxies as subscribed
            // (instance, shouldSubscribe)
            registerHubProxies(proxies, true);

            this._registerSubscribedHubs();
        }).disconnected(function () {
            // Unsubscribe all hub proxies when we "disconnect".  This is to ensure that we do not re-add functional call backs.
            // (instance, shouldSubscribe)
            registerHubProxies(proxies, false);
        });

        proxies['auctionHub'] = this.createHubProxy('auctionHub'); 
        proxies['auctionHub'].client = { };
        proxies['auctionHub'].server = {
            auctionNewOffer: function (que) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["AuctionNewOffer"], $.makeArray(arguments)));
             },

            getAuctionOffers: function (auctionId) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["GetAuctionOffers"], $.makeArray(arguments)));
             },

            getCommisionForAuction: function (auctionId) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["GetCommisionForAuction"], $.makeArray(arguments)));
             },

            getNextOffer: function (auctionId) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["GetNextOffer"], $.makeArray(arguments)));
             },

            refreshUsersPage: function (userId, hubContext) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["RefreshUsersPage"], $.makeArray(arguments)));
             },

            revertLastOffer: function (auctionId, userId) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["RevertLastOffer"], $.makeArray(arguments)));
             },

            revertLastOfferFromAdmin: function (auctionId) {
                return proxies['auctionHub'].invoke.apply(proxies['auctionHub'], $.merge(["RevertLastOfferFromAdmin"], $.makeArray(arguments)));
             }
        };

        proxies['chatHub'] = this.createHubProxy('chatHub'); 
        proxies['chatHub'].client = { };
        proxies['chatHub'].server = {
            getAllGroupedBySubject: function () {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetAllGroupedBySubject"], $.makeArray(arguments)));
             },

            getAllMessagesForUserList: function (userId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetAllMessagesForUserList"], $.makeArray(arguments)));
             },

            getAllUnreadForDestinationSubjectAndWarehouseItem: function (subjectId, warehouseItemId, dateRead) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetAllUnreadForDestinationSubjectAndWarehouseItem"], $.makeArray(arguments)));
             },

            getAllUnreadForSourceSubjectAndWarehouseItem: function (subjectId, warehouseItemId, dateRead) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetAllUnreadForSourceSubjectAndWarehouseItem"], $.makeArray(arguments)));
             },

            getForWarehouseId: function (warehouseItemId, childSubjects, isAdmin) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetForWarehouseId"], $.makeArray(arguments)));
             },

            getLastMessageForSingleUser: function (sourceUserId, destUserId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetLastMessageForSingleUser"], $.makeArray(arguments)));
             },

            getNotReadMessageForSingleUser: function (sourceUserId, destUserId, dateRead) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["GetNotReadMessageForSingleUser"], $.makeArray(arguments)));
             },

            isUserOnline: function (userid) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["IsUserOnline"], $.makeArray(arguments)));
             },

            sendMessage: function (message, userId, sender, warehouseItemId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SendMessage"], $.makeArray(arguments)));
             },

            sendMessageFromDataChange: function (message, userId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SendMessageFromDataChange"], $.makeArray(arguments)));
             },

            sendNewFileMessage: function (userId, warehouseItemId, subjectId, fileName, fileType, hubContext, attachementType) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SendNewFileMessage"], $.makeArray(arguments)));
             },

            sendNewFileMessageForBuyer: function (userId, warehouseItemId, subjectId, fileName, fileType, hubContext) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SendNewFileMessageForBuyer"], $.makeArray(arguments)));
             },

            sendNewOrderMessage: function (message, title, userName, orderId, warehouseItemId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SendNewOrderMessage"], $.makeArray(arguments)));
             },

            sendNewStatusMessage: function (message, title, fullName, userName, warehouseItemId, subjectId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SendNewStatusMessage"], $.makeArray(arguments)));
             },

            setViewedDateForAdmins: function (msg, subjectId, warehouseItemId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SetViewedDateForAdmins"], $.makeArray(arguments)));
             },

            setViewedDateForSubjectUsers: function (msg, subjectId, warehouseItemId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SetViewedDateForSubjectUsers"], $.makeArray(arguments)));
             },

            setViewedDateForUser: function (msg, userId) {
                return proxies['chatHub'].invoke.apply(proxies['chatHub'], $.merge(["SetViewedDateForUser"], $.makeArray(arguments)));
             }
        };

        return proxies;
    };

    signalR.hub = $.hubConnection("/signalr", { useDefaultPath: false });
    $.extend(signalR, signalR.hub.createHubProxies());

}(window.jQuery, window));
