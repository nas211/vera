/**
 * Hardhat contract tests for the Stage 2 candidates (VelaRewardVault + VelaRewardVaultFactory).
 * Mirrors src/lib/__tests__/rewardModel.test.ts (the TypeScript oracle).
 *
 * NOT part of the Vitest suite: the `.hardhat.ts` suffix keeps it out of Vitest's default
 * `*.test.ts` include. It needs a Hardhat workspace (hardhat, @nomicfoundation/hardhat-toolbox,
 * solc ^0.8.20) whose sources point at contracts/candidate and whose tests point at this folder.
 * Runs only on the local in-process Hardhat network (chainId 31337) — never against DogeOS.
 *
 * Timing: every state-changing tx is pinned with setNextBlockTimestamp, and rewards are measured
 * as reserveBalance deltas, so results never depend on eth_call block-time semantics.
 */
import { expect } from 'chai';
import { ethers } from 'hardhat';
import { time } from '@nomicfoundation/hardhat-network-helpers';

const DOGE = 10n ** 18n;
const DAY = 86_400;
const YEAR = 365n * 24n * 3600n;
const BPS = 10_000n;
const DOGEOS_TESTNET = 6_281_971n;
const dueFor = (p: bigint, apr: bigint, dt: number) => (p * apr * BigInt(dt)) / (BPS * YEAR);
const near = (got: bigint, want: bigint, tol = 2n) => {
  expect(got <= want, `got ${got} > want ${want}`).to.equal(true);
  expect(got >= want - tol, `got ${got} < want ${want} - ${tol}`).to.equal(true);
};

async function setup(apr = 500n) {
  const [owner, a, b, c, mallory] = await ethers.getSigners();
  const { chainId } = await ethers.provider.getNetwork();
  const factory = await (await ethers.getContractFactory('VelaRewardVaultFactory')).deploy(chainId);
  await factory.createVault(owner.address, apr);
  const vault = await ethers.getContractAt('VelaRewardVault', await factory.vaultAt(0));
  const T0 = (await time.latest()) + 1_000;
  /** run `fn` in a block mined at exactly `t` */
  const at = async <R>(t: number, fn: () => Promise<R>) => { await time.setNextBlockTimestamp(t); return fn(); };
  const as = (s: typeof a) => vault.connect(s) as typeof vault;
  /** claim at exact time t, return amount paid out of the reserve */
  const claimAt = async (s: typeof a, t: number) => {
    const before: bigint = await vault.reserveBalance();
    await at(t, () => as(s).claim());
    return before - (await vault.reserveBalance());
  };
  return { factory, vault, owner, a, b, c, mallory, chainId, T0, at, as, claimAt };
}

async function assertInvariants(vault: Awaited<ReturnType<typeof setup>>['vault'], users: string[]) {
  const [principal, reserve, liab, funded, claimed] = await Promise.all([
    vault.totalPrincipal(), vault.reserveBalance(), vault.liabilities(), vault.totalFunded(), vault.totalClaimed(),
  ]);
  const bal = await ethers.provider.getBalance(await vault.getAddress());
  expect(bal).to.equal(principal + reserve); // principal and reserve fully backed, never mixed
  expect(liab <= reserve).to.equal(true);
  expect(funded).to.equal(reserve + claimed);
  let sumP = 0n; let sumOwed = 0n;
  for (const u of users) { const p = await vault.positions(u); sumP += p[0]; sumOwed += p[2]; }
  expect(sumP).to.equal(principal);
  expect(sumOwed <= liab).to.equal(true);
}

describe('VelaRewardVaultFactory (candidate)', () => {
  it('only the owner creates vaults; vault is registered, owned by the requested owner and chain-bound', async () => {
    const { factory, vault, owner, a, mallory, chainId } = await setup();
    await expect(factory.connect(mallory).createVault(mallory.address, 500n))
      .to.be.revertedWithCustomError(factory, 'OnlyOwner');
    expect(await factory.vaultCount()).to.equal(1n);
    expect(await factory.isVault(await vault.getAddress())).to.equal(true);
    expect(await vault.owner()).to.equal(owner.address);
    expect(await vault.factory()).to.equal(await factory.getAddress());
    expect(await vault.aprBps()).to.equal(500n);
    expect(await vault.expectedChainId()).to.equal(chainId);
    await expect(factory.createVault(a.address, 250n)).to.emit(factory, 'VaultCreated');
    const second = await ethers.getContractAt('VelaRewardVault', await factory.vaultAt(1));
    expect(await second.owner()).to.equal(a.address); // isolated vault, separate owner
    expect(await second.reserveBalance()).to.equal(0n);
  });

  it('rejects zero owner, APR > 100%, and deployment on the wrong chain', async () => {
    const { factory, owner } = await setup();
    await expect(factory.createVault(ethers.ZeroAddress, 500n)).to.be.revertedWithCustomError(factory, 'ZeroAddress');
    await expect(factory.createVault(owner.address, 10_001n)).to.be.revertedWithCustomError(factory, 'RateOutOfRange');
    const F = await ethers.getContractFactory('VelaRewardVaultFactory');
    await expect(F.deploy(DOGEOS_TESTNET)).to.be.revertedWithCustomError(F, 'WrongChain');
    const V = await ethers.getContractFactory('VelaRewardVault');
    await expect(V.deploy(owner.address, 500n, DOGEOS_TESTNET)).to.be.revertedWithCustomError(V, 'WrongChain');
  });
});

describe('VelaRewardVault (candidate)', () => {
  it('pays simple APR only from the funded reserve; principal returned in full', async () => {
    const { vault, a, T0, at, as, claimAt } = await setup();
    await at(T0, () => vault.topUpReserve({ value: 100n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: 1_000n * DOGE }));
    const paid = await claimAt(a, T0 + 1 + 30 * DAY);
    near(paid, dueFor(1_000n * DOGE, 500n, 30 * DAY));
    expect(await vault.totalPrincipal()).to.equal(1_000n * DOGE);
    await assertInvariants(vault, [a.address]);
    await at(T0 + 2 + 30 * DAY, () => as(a).withdraw(1_000n * DOGE));
    expect(await vault.totalPrincipal()).to.equal(0n);
    expect(await vault.reserveBalance()).to.equal(100n * DOGE - paid);
    await assertInvariants(vault, [a.address]);
  });

  it('principal is withdrawable with an empty reserve and nothing is claimable', async () => {
    const { vault, a, T0, at, as } = await setup();
    await at(T0, () => as(a).deposit({ value: 10n * DOGE }));
    await time.setNextBlockTimestamp(T0 + 30 * DAY);
    await expect(as(a).claim()).to.be.revertedWithCustomError(vault, 'NothingToClaim');
    await at(T0 + 30 * DAY + 1, () => as(a).withdraw(10n * DOGE));
    expect(await ethers.provider.getBalance(await vault.getAddress())).to.equal(0n);
    await assertInvariants(vault, [a.address]);
  });

  it('gives no retroactive rewards to late deposits', async () => {
    const { vault, a, b, T0, at, as, claimAt } = await setup();
    await at(T0, () => vault.topUpReserve({ value: 100n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: 1_000n * DOGE }));
    await at(T0 + 1 + 30 * DAY, () => as(b).deposit({ value: 1_000n * DOGE }));
    const bPaid = await claimAt(b, T0 + 1 + 40 * DAY);
    near(bPaid, dueFor(1_000n * DOGE, 500n, 10 * DAY));
    expect(bPaid < dueFor(1_000n * DOGE, 500n, 40 * DAY) / 2n).to.equal(true);
    await assertInvariants(vault, [a.address, b.address]);
  });

  it('prevents double claims', async () => {
    const { vault, a, T0, at, as, claimAt } = await setup();
    await at(T0, () => vault.topUpReserve({ value: 10n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: 1_000n * DOGE }));
    // freeze accrual (setRate checkpoints first) so the second claim sees exactly zero new rewards
    await at(T0 + 1 + DAY, () => vault.setRate(0n));
    near(await claimAt(a, T0 + 2 + DAY), dueFor(1_000n * DOGE, 500n, DAY));
    await time.setNextBlockTimestamp(T0 + 3 + DAY);
    await expect(as(a).claim()).to.be.revertedWithCustomError(vault, 'NothingToClaim');
  });

  it('caps accrual at funded capacity and never backfills unfunded time after a top-up', async () => {
    const { vault, a, T0, at, as, claimAt } = await setup();
    await at(T0, () => vault.topUpReserve({ value: 1n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: 5_000n * DOGE }));
    await at(T0 + 1 + 10 * DAY, () => vault.topUpReserve({ value: 100n * DOGE })); // checkpoints first
    expect(await vault.liabilities()).to.equal(1n * DOGE); // capped, not 10 days' worth
    const paid = await claimAt(a, T0 + 1 + 11 * DAY);
    near(paid, 1n * DOGE + dueFor(5_000n * DOGE, 500n, DAY)); // only post-top-up time added
    await assertInvariants(vault, [a.address]);
  });

  it('checkpoints before rate changes (old rate for old time)', async () => {
    const { vault, a, T0, at, as, claimAt } = await setup();
    await at(T0, () => vault.topUpReserve({ value: 100n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: 1_000n * DOGE }));
    await at(T0 + 1 + 10 * DAY, () => vault.setRate(1_000n));
    const paid = await claimAt(a, T0 + 1 + 20 * DAY);
    near(paid, dueFor(1_000n * DOGE, 500n, 10 * DAY) + dueFor(1_000n * DOGE, 1_000n, 10 * DAY));
  });

  it('owner-authorized seizure moves principal only; accrued rewards stay with the user', async () => {
    const { vault, a, c, T0, at, as, claimAt } = await setup();
    await at(T0, () => vault.topUpReserve({ value: 100n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: 1_000n * DOGE }));
    await time.setNextBlockTimestamp(T0 + 1 + 10 * DAY);
    await expect(vault.seize(a.address, 1_000n * DOGE, ethers.ZeroAddress))
      .to.be.revertedWithCustomError(vault, 'ZeroAddress');
    const before = await ethers.provider.getBalance(c.address);
    await at(T0 + 2 + 10 * DAY, () => vault.seize(a.address, 1_000n * DOGE, c.address));
    expect((await ethers.provider.getBalance(c.address)) - before).to.equal(1_000n * DOGE);
    expect(await vault.reserveBalance()).to.equal(100n * DOGE); // reserve untouched by seizure
    near(await claimAt(a, T0 + 50 * DAY), dueFor(1_000n * DOGE, 500n, 10 * DAY + 1));
    await assertInvariants(vault, [a.address]);
  });

  it('owner-only top-up, rate change and seizure; rate bounded', async () => {
    const { vault, a, mallory, as } = await setup();
    await as(a).deposit({ value: DOGE });
    await expect(as(mallory).topUpReserve({ value: DOGE })).to.be.revertedWithCustomError(vault, 'OnlyOwner');
    await expect(as(mallory).setRate(1_000n)).to.be.revertedWithCustomError(vault, 'OnlyOwner');
    await expect(as(mallory).seize(a.address, DOGE, mallory.address)).to.be.revertedWithCustomError(vault, 'OnlyOwner');
    await expect(vault.setRate(10_001n)).to.be.revertedWithCustomError(vault, 'RateOutOfRange');
  });

  it('rejects plain native transfers to vault and factory', async () => {
    const { vault, factory, a } = await setup();
    await expect(a.sendTransaction({ to: await vault.getAddress(), value: DOGE }))
      .to.be.revertedWithCustomError(vault, 'DirectTransferRejected');
    await expect(a.sendTransaction({ to: await factory.getAddress(), value: DOGE }))
      .to.be.revertedWithCustomError(factory, 'DirectTransferRejected');
  });

  it('rejects zero amounts and over-withdrawal (cannot pull reserve as principal)', async () => {
    const { vault, a, as } = await setup();
    await vault.topUpReserve({ value: 50n * DOGE });
    await as(a).deposit({ value: DOGE });
    await expect(as(a).deposit({ value: 0n })).to.be.revertedWithCustomError(vault, 'ZeroAmount');
    await expect(vault.topUpReserve({ value: 0n })).to.be.revertedWithCustomError(vault, 'ZeroAmount');
    await expect(as(a).withdraw(DOGE + 1n)).to.be.revertedWithCustomError(vault, 'Insufficient');
    await expect(as(a).withdraw(0n)).to.be.revertedWithCustomError(vault, 'Insufficient');
  });

  it('keeps sum(owed) <= liabilities <= reserve under awkward integer rounding', async () => {
    const { vault, a, b, c, T0, at, as, claimAt } = await setup();
    const users = [a, b, c];
    const addrs = users.map((u) => u.address);
    await at(T0, () => vault.topUpReserve({ value: 3n * DOGE + 7n }));
    const amts = [DOGE + 1n, 2n * DOGE + 3n, 3n * DOGE + 7n];
    let t = T0;
    for (let i = 0; i < 3; i++) {
      t += 12_345; await at(t, () => as(users[i]).deposit({ value: amts[i] * 1_000n }));
      await assertInvariants(vault, addrs);
    }
    t += 7_777_777; await at(t, () => as(b).withdraw(999n)); await assertInvariants(vault, addrs);
    t += 3_333; await at(t, () => vault.setRate(777n)); await assertInvariants(vault, addrs);
    let claimed = 0n;
    for (const u of users) { t += 1; claimed += await claimAt(u, t); await assertInvariants(vault, addrs); }
    expect(claimed <= (await vault.totalFunded())).to.equal(true);
  });

  // Regression: floored per-checkpoint liabilities lost < 1 wei per checkpoint while users settle on
  // floor(principal * accPerShare / PREC), so claim() reverted with ReserveShortfall after >= 2 checkpoints.
  it('regression: repeated checkpoints never let owed exceed liabilities; claim succeeds', async () => {
    const { vault, a, T0, at, as, claimAt } = await setup();
    const p = 3n * DOGE + 7n; // coprime to 10 -> sub-wei remainder on every checkpoint
    await at(T0, () => vault.topUpReserve({ value: 10n * DOGE }));
    await at(T0 + 1, () => as(a).deposit({ value: p }));
    const due = dueFor(p, 500n, DAY);
    for (let d = 1; d <= 3; d++) {
      await at(T0 + 1 + d * DAY, () => vault.checkpoint());
      expect(await vault.liabilities()).to.equal(BigInt(d) * due); // ceil(pay - sub-wei) == pay, never more
      await assertInvariants(vault, [a.address]);
    }
    // freeze accrual (setRate checkpoints first) so the claim block adds nothing new
    await at(T0 + 1 + 4 * DAY, () => vault.setRate(0n));
    const reserved: bigint = await vault.liabilities();
    const paid = await claimAt(a, T0 + 2 + 4 * DAY); // the floored version reverts ReserveShortfall here
    expect(paid <= reserved).to.equal(true);
    near(paid, 4n * due, 4n);
    expect(await vault.liabilities()).to.equal(reserved - paid);
    await assertInvariants(vault, [a.address]);
  });
});
