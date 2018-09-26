from transaction import *
from merkle_tree import *
import datetime, hashlib, ecdsa, statistics, json, random

class Block:
    def __init__(self, transactions, header):
        self._transactions = transactions
        self._header = header

    @classmethod
    def hash_header(cls, header):
        inp = json.dumps(header).encode()
        interm = hashlib.sha256(inp).digest()
        return hashlib.sha256(interm).hexdigest()

    @classmethod
    def new(cls, transactions, prev_hash):
        root = MerkleTree(transactions).get_root()
        header = {
            "prev_hash": prev_hash,
            "root": root,
            "timestamp": datetime.datetime.utcnow().timestamp(),
            "nonce": 0
        }
        while True:
            header_hash = Block.hash_header(header)
            if header_hash < Blockchain.TARGET:
                return cls(transactions, header)
            header["nonce"] += 1

    @property
    def transactions(self):
        return self._transactions

    @property
    def header(self):
        return self._header

class Blockchain:
    _zeroes = "000"
    TARGET = _zeroes + (64 - len(_zeroes)) * "f"

    def __init__(self, hash_block_map, endhash_clen_map):
        self._hash_block_map = hash_block_map
        self._endhash_clen_map = endhash_clen_map

    @classmethod
    def new(cls, genesis=None):
        if not genesis:
            genesis = Block.new(None, None)
        genesis_hash = Block.hash_header(genesis.header)
        hash_block_map = { genesis_hash: genesis }
        # Keep track of end blocks and chain length
        endhash_clen_map = { genesis_hash: 0 }
        return cls(hash_block_map, endhash_clen_map)


    def _get_chain_length(self, block):
        # Compute chain length from block
        prev_hash = block.header["prev_hash"]
        chain_len = 0
        while prev_hash != None:
            for b in self._hash_block_map.values():
                if prev_hash == Block.hash_header(b.header):
                    prev_hash = b.header["prev_hash"]
                    chain_len += 1
                    break
        return chain_len

    def add(self, block):
        # Add new block to chain 
        # Validate block 
        if not self.validate(block):
            raise Exception("Invalid block.")
        curr_hash = Block.hash_header(block.header)
        prev_hash = block.header["prev_hash"]
        self._hash_block_map[curr_hash] = block
        # If the previous block is one of the last blocks,
        # replace the previous hash out with the current one
        # ie. make the current block be one of the last blocks,
        # and increment its chain length
        if prev_hash in self._endhash_clen_map.keys():
            chain_len = self._endhash_clen_map.pop(prev_hash)
            self._endhash_clen_map[curr_hash] = chain_len + 1
        # Else, compute the chain length by traversing backwards
        # and add the current block hash into dictionary
        else:
            self._endhash_clen_map[curr_hash] = self._get_chain_length(block)

    def _prev_exist(self, prev_hash):
        # Check block with prev_hash exist in list
        for b in self._hash_block_map.values():
            if prev_hash == Block.hash_header(b.header):
                return True
        return False

    def _check_timestamp(self, block):
        # Check timestamp larger than median of previous 11 timestamps
        prev_timestamps = []
        prev_hash = block.header["prev_hash"]
        while prev_hash != None and len(prev_timestamps) < 11:
            b = self._hash_block_map[prev_hash]
            prev_timestamps.append(b.header["timestamp"])
            prev_hash = b.header["prev_hash"]
        return block.header["timestamp"] > statistics.median(prev_timestamps)

    def validate(self, block):
        # Validate the block
        # Check header hash matches and is smaller than Blockchain.TARGET
        comp_hash = Block.hash_header(block.header)
        if comp_hash >= Blockchain.TARGET:
            print("Error: Invalid header hash in block")
            return False
        prev_hash = block.header["prev_hash"]
        # Check previous block exist in blocks list
        if not self._prev_exist(prev_hash):
            print("Error: Previous block does not exist.")
            return False
        # Check timestamp validity
        if not self._check_timestamp(block):
            print("Error: Invalid timestamp in block.")
            return False
        return True

    def _get_chain_pow(self, block):
        # Compute chain length from block
        prev_hash = block.header["prev_hash"]
        chain_pow = block.header["nonce"]
        while prev_hash != None:
            for b in self._hash_block_map.values():
                if prev_hash == Block.hash_header(b.header):
                    prev_hash = b.header["prev_hash"]
                    chain_pow += b.header["nonce"]
                    break
        return chain_pow

    def _pow(block_hash):
        return self._get_chain_pow(self._hash_block_map[block_hash])

    def resolve(self):
        # Resolve potential forks in block
        # No forks
        if len(self._endhash_clen_map) == 1:
            blk_hash = list(self._endhash_clen_map.keys())[0]
            return self._hash_block_map[blk_hash]
        # Get hashes of end-blocks with longest chain length
        longest_clen = max(self._endhash_clen_map.values())
        block_hashes = [
            k for k, v in self._endhash_clen_map.items() if v == longest_clen
        ]
        # Multiple chain with same length exist, 
        # use PoW ie. nonce to determine which to keep
        if len(block_hashes) != 1:
            block_hashes = [ max(block_hashes, key=lambda bh: self._pow(bh)) ]
        # Remove all blocks beloging to forks
        blk_hash = block_hashes[0]
        blk = self._hash_block_map[blk_hash]
        new_hash_block_map = { blk_hash: blk }
        prev_hash = blk.header["prev_hash"]
        while prev_hash != None:
            b = self._hash_block_map[prev_hash]
            new_hash_block_map[prev_hash] = b
            prev_hash = b.header["prev_hash"]
        self._endhash_clen_map = { block_hashes[0]: longest_clen }
        self._hash_block_map = new_hash_block_map
        return blk

    @property
    def hash_block_map(self):
        return self._hash_block_map

    @property
    def endhash_clen_map(self):
        return self._endhash_clen_map

if __name__ == "__main__":
    blockchain = Blockchain.new()
    hashes = []
    for i in range(10):
        transactions = []
        for j in range(10):
            sender_sk = ecdsa.SigningKey.generate()
            sender_vk = sender_sk.get_verifying_key()
            receiver_sk = ecdsa.SigningKey.generate()
            receiver_vk = receiver_sk.get_verifying_key()
            t = Transaction.new(sender_vk, receiver_vk, j+1, sender_sk, str(j))
            transactions.append(t.to_json())
        prev_block = blockchain.resolve()
        prev_hash = Block.hash_header(prev_block.header)
        new_block = Block.new(transactions, prev_hash)
        hashes.append(Block.hash_header(new_block.header))
        blockchain.add(new_block)
    prev_hash = hashes[2]
    for i in range(4):
        fork_block = Block.new(transactions, prev_hash)
        blockchain.add(fork_block)
        prev_hash = Block.hash_header(fork_block.header)
    print("Pre-resolve: " + str(blockchain.endhash_clen_map))
    blockchain.resolve()
    print("Post-resolve: " + str(blockchain.endhash_clen_map))

