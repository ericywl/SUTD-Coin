"""Double spending demonstration"""
import sys
import os
import time
import json
import threading

import algo
from spv_client import SPVClient, _SPVClientListener
from miner import Miner, _MinerListener
from transaction import Transaction
from block import Block


class _DoubleSpendMinerListener(_MinerListener):
    """DoubleSpendMinerListener class"""

    def _handle_block(self, data, client_sock):
        blk_json = json.loads(data[1:])["blk_json"]
        super()._handle_block(data, client_sock)
        self._worker.ds_handle_block(blk_json)

    def _handle_transaction(self, data, client_sock):
        # Receive new transaction
        tx_json = json.loads(data[1:])["tx_json"]
        client_sock.close()
        # ds_handle_transaction will return True if the transaction is
        # excluded by the worker, else False
        if not self._worker.ds_handle_transaction(tx_json):
            # Handle as per usual
            super()._handle_transaction(data, None)


class DoubleSpendMiner(Miner):
    """DoubleSpendMiner class"""
    INIT_MODE = 0  # Initial mode
    FORK_MODE = 1  # Start creating private blockchain fork
    FIRE_MODE = 2  # Start thinking about publishing all withheld blocks

    def __init__(self, privkey, pubkey, address):
        super().__init__(privkey, pubkey, address, _DoubleSpendMinerListener)
        self.mode = DoubleSpendMiner.INIT_MODE
        self.excluded_transactions = set()
        self.fork_block = None
        self.withheld_blocks = []
        self.pubchain_count = 0
        # Thread locks
        self.withheld_blk_lock = threading.RLock()
        self.pubchain_count_lock = threading.RLock()

    def _get_tx_pool(self):
        return super()._get_tx_pool() - self.excluded_transactions

    def create_block(self, prev_hash=None):
        if self.mode is not DoubleSpendMiner.INIT_MODE:
            if self.withheld_blocks:
                with self.withheld_blk_lock:
                    blk = self.withheld_blocks[-1]
                prev_hash = algo.hash1_dic(blk.header)
            else:
                prev_hash = algo.hash1_dic(self.fork_block.header)
            # do a dumb wait here
            while prev_hash not in self._blockchain.hash_block_map.keys():
                time.sleep(0.1)
            return super().create_block(prev_hash)
        return super().create_block()

    def _broadcast_block(self, block):
        if self.mode == DoubleSpendMiner.FORK_MODE:
            with self.withheld_blk_lock:
                self.withheld_blocks.append(block)
        elif self.mode == DoubleSpendMiner.FIRE_MODE:
            # Start thinking of firing
            self.withheld_blk_lock.acquire()
            self.pubchain_count_lock.acquire()
            try:
                self.withheld_blocks.append(block)
                if len(self.withheld_blocks) > self.pubchain_count:
                    self.push_blocks()
            finally:
                self.withheld_blk_lock.release()
                self.pubchain_count_lock.release()
        else:
            super()._broadcast_block(block)

    def push_blocks(self):
        """Publish all the blocks in withheld blocks list"""
        print("BOMBS AWAY")
        with self.withheld_blk_lock:
            for blk in self.withheld_blocks:
                super()._broadcast_block(blk)
            self.withheld_blocks = []
            self.mode = DoubleSpendMiner.INIT_MODE

    def ds_handle_block(self, blk_json):
        """Handle received block from listener"""
        if self.mode == DoubleSpendMiner.INIT_MODE:
            blk = Block.from_json(blk_json)
            bad_spv = self.find_peer_by_clsname("DoubleSpendSPVClient")
            # Activate FORK_MODE if block contains badSPV transaction
            # ie. badSPV got the money from DoubleSpendMiner
            for tx_json in blk.transactions:
                blk_tx = Transaction.from_json(tx_json)
                if (blk_tx.sender == self.pubkey and
                        blk_tx.receiver == bad_spv["pubkey"]):
                    self.mode = DoubleSpendMiner.FORK_MODE
                    self.fork_block = blk
                    print("FORK_MODE ACTIVATED AT", blk)
                    break
        else:
            # Maintain public blockchain length from fork point
            with self.pubchain_count_lock:
                self.pubchain_count += 1

    def ds_handle_transaction(self, tx_json):
        """Handle received transaction from listener"""
        recv_tx = Transaction.from_json(tx_json)
        bad_spv = self.find_peer_by_clsname("DoubleSpendSPVClient")
        vendor = self.find_peer_by_clsname("Vendor")
        if self.mode == DoubleSpendMiner.FORK_MODE:
            # Activate FIRE_MODE if transaction is from bad SPV (double spend)
            if (recv_tx.sender == bad_spv["pubkey"]
                    and recv_tx.receiver == self.pubkey):
                self.mode = DoubleSpendMiner.FIRE_MODE
                print("FIRE_MODE ACTIVATED")
        if (recv_tx.sender == bad_spv["pubkey"]
                and recv_tx.receiver == vendor["pubkey"]):
            # Exclude vendor transaction
            self.excluded_transactions.add(tx_json)
            return True
        return False


class _DoubleSpendSPVClientListener(_SPVClientListener):
    def handle_client_data(self, data, client_sock):
        if data[0].lower() == "p":
            # Vendor sent product, so send coins back to DoubleSpendMiner
            bad_miner = self._worker.find_peer_by_clsname("DoubleSpendMiner")
            self._worker.create_transaction(bad_miner["pubkey"],
                                            Vendor.PRODUCT_PRICE,
                                            comment="DoubleSpend")
            print("SPV Client got IPad, give dirty money to DoubleSpendMiner")
            client_sock.close()
        else:
            # Handle as per usual
            super().handle_client_data(data, client_sock)


class DoubleSpendSPVClient(SPVClient):
    """DoubleSpendSPVClient class"""
    # The miner cannot be the one doing the transaction with the vendor
    # because the miner can earn rewards from mining and those earned rewards
    # will be able to compensate for the "cheated" amount

    def __init__(self, privkey, pubkey, address):
        super().__init__(privkey, pubkey, address, _DoubleSpendSPVClientListener)


class Vendor(SPVClient):
    """Vendor class"""
    PRODUCT_PRICE = 50

    def send_product(self, tx_hash):
        """Simulate delivering the product to a buyer"""
        # Make new protocol prefix so buyer can receive this message?
        # Used by DoubleSpendMiner to determine when to start forking
        tx_json = self._hash_transactions_map[tx_hash]
        obtained_tx = Transaction.from_json(tx_json)
        buyer = self.find_peer_by_pubkey(obtained_tx.sender)
        Vendor._send_message("p" + tx_hash, buyer["address"])
        print(f"{self.__class__.__name__} sent product to {buyer['name']}")


# Only used in test()
def map_pubkey_to_name(obs):
    """Map pubkey to name in balance"""
    name_balance = {}
    for key, val in obs.balance.items():
        items = [x for x in obs.peers if x["pubkey"] == key]
        if key == obs.pubkey:
            name_balance[obs.name] = val
        elif items:
            item = items[0]
            name_balance[item["name"]] = val
    return name_balance


def main():
    """Main function"""
    try:
        if sys.argv[2] == "MINER":
            miner = DoubleSpendMiner.new(("127.0.0.1", int(sys.argv[1])))
            miner.startup()
            print("DoubleSpendMiner started..")
            while not os.path.exists("mine_lock"):
                time.sleep(0.5)
            # Try to get coins by mining
            while miner.pubkey not in miner.balance or \
                    miner.balance[miner.pubkey] < Vendor.PRODUCT_PRICE:
                if miner.create_block():
                    print(miner.blockchain.endhash_clen_map)
                time.sleep(1)
            # Send coins to badSPV
            print(
                f"DoubleSpendMiner send {Vendor.PRODUCT_PRICE} coins to BadSPVClient")
            bad_spv = miner.find_peer_by_clsname("DoubleSpendSPVClient")
            miner.create_transaction(bad_spv["pubkey"], Vendor.PRODUCT_PRICE)
            # Wait for badSPV to get the coins, then start forking
            while True:
                if miner.mode is not DoubleSpendMiner.INIT_MODE:
                    if miner.create_block():
                        print(miner.blockchain.endhash_clen_map)
                time.sleep(1)

        elif sys.argv[2] == "VENDOR":
            vendor = Vendor.new(("127.0.0.1", int(sys.argv[1])))
            vendor.startup()
            print("Vendor started..")
            while not vendor.transactions:
                time.sleep(1)
            for vtx in vendor.transactions:
                vtx_hash = algo.hash1(vtx)
                while not vendor.verify_transaction_proof(vtx_hash):
                    time.sleep(2)
                vendor.send_product(vtx_hash)
            while True:
                for vtx in vendor.transactions:
                    vtx_hash = algo.hash1(vtx)
                    check = vendor.verify_transaction_proof(vtx_hash)
                    print(f"Verify vendor transaction in blockchain: {check}")
                time.sleep(5)

        elif sys.argv[2] == "SPV":
            spv = DoubleSpendSPVClient.new(("127.0.0.1", int(sys.argv[1])))
            spv.startup()
            print("BadSPVClient started, waiting for "
                  + "coins from BadMiner to spend.")
            balance = 0
            while balance < Vendor.PRODUCT_PRICE:
                balance = spv.request_balance()
                time.sleep(1)
            print("BadSPVClient feeling rich now, going to buy IPad")
            vendor = spv.find_peer_by_clsname("Vendor")
            spv.create_transaction(
                vendor["pubkey"], Vendor.PRODUCT_PRICE, "Never gonna give you up")
    except IndexError:
        print("Not enough arguments provided.")


if __name__ == "__main__":
    main()
